"""Unified structured-output Responses API + retry for all LLM tasks."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel

from config_models import AppConfig, DEFAULT_MODEL, normalize_qid
from token_usage import TokenUsage
from llm_client import temperature_for_model

T = TypeVar("T")
_T = TypeVar("_T")
_R = TypeVar("_R")

# Application-level retries when validation fails.
MAX_VALIDATION_RETRIES = 2
MAX_JSON_LLM_ATTEMPTS = MAX_VALIDATION_RETRIES + 1


# ---------------------------------------------------------------------------
# Shared LLM context — resolved once per orchestrator, passed to workers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LlmContext:
    """Resolved LLM task context shared across per-job workers."""

    logger: logging.Logger
    client: OpenAI
    model: str
    system_prompt: str
    workers: int
    max_completion_tokens: int


def load_llm_context(
    config: AppConfig,
    prompt_name: str,
    *,
    model_selector: Callable[[AppConfig], str] | None = None,
    client: OpenAI | None = None,
    max_completion_tokens: int | None = None,
) -> LlmContext:
    """Resolve config into a ready-to-use :class:`LlmContext`.

    Replaces the repeated setup boilerplate in LLM task orchestrators.
    """
    # Local imports to avoid circular dependencies (same pattern as elsewhere).
    from llm_client import get_openai_client
    from prompt_builder import load_prompt
    from utils import get_job_logger

    if model_selector is not None:
        model = model_selector(config)
    else:
        model = config.model or DEFAULT_MODEL
    return LlmContext(
        logger=get_job_logger(config, prompt_name),
        client=client or get_openai_client(),
        model=model,
        system_prompt=load_prompt(prompt_name, assignment_name=config.assignment_name),
        workers=config.workers,
        max_completion_tokens=max_completion_tokens or config.max_completion_tokens,
    )


def retry_with_exponential_backoff(
    operation: Callable[[], T],
    *,
    max_attempts: int,
    on_before_retry: Callable[[int, float], None] | None = None,
    on_attempt_failed: Callable[[int, BaseException], None] | None = None,
) -> tuple[T | None, BaseException | None]:
    """Run ``operation`` up to ``max_attempts`` times with exponential backoff."""
    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        if attempt > 0:
            delay = float(2**attempt)
            if on_before_retry is not None:
                on_before_retry(attempt, delay)
            time.sleep(delay)
        try:
            return (operation(), None)
        except BaseException as e:
            last_error = e
            if on_attempt_failed is not None:
                on_attempt_failed(attempt, e)
    return (None, last_error)


def run_jobs(
    items: Iterable[_T],
    worker: Callable[[_T], _R],
    *,
    max_workers: int,
) -> Iterator[_R]:
    """Run ``worker(item)`` for each item, sequentially or in parallel.

    When ``max_workers <= 1`` or there's only one item, runs sequentially.
    Otherwise uses a thread pool. Results arrive in arbitrary order when parallel.
    """
    work = list(items)
    if not work:
        return
    if max_workers <= 1 or len(work) <= 1:
        for item in work:
            yield worker(item)
    else:
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
            futures = {pool.submit(worker, item): item for item in work}
            for fut in as_completed(futures):
                yield fut.result()


def complete_structured(
    client: OpenAI,
    *,
    model: str,
    messages: list,
    max_completion_tokens: int,
    response_model: type[BaseModel],
) -> tuple[BaseModel, TokenUsage]:
    """Run structured outputs via Responses API; return ``(parsed_model, token_usage)``."""
    temperature = temperature_for_model(model)
    response = client.responses.parse(
        model=model,
        input=messages,
        text_format=response_model,
        max_output_tokens=max_completion_tokens,
        temperature=temperature,
    )
    for output in response.output:
        if output.type == "message":
            for content in output.content:
                if content.type == "refusal":
                    raise JsonLlmParseError(f"Model refused: {content.refusal}")
    parsed = response.output_parsed
    if parsed is None:
        raise JsonLlmParseError("Structured output returned None")
    usage = TokenUsage()
    if getattr(response, "usage", None):
        u = response.usage
        usage = TokenUsage(
            prompt_tokens=getattr(u, "input_tokens", 0) or 0,
            completion_tokens=getattr(u, "output_tokens", 0) or 0,
        )
    return parsed, usage


class JsonLlmError(Exception):
    """Base for JSON LLM pipeline failures."""


class JsonLlmApiError(JsonLlmError):
    """OpenAI / transport failure before a usable response body."""


class JsonLlmParseError(JsonLlmError):
    """Response body could not be parsed or converted to the domain type."""


class JsonLlmValidationError(JsonLlmError):
    """Parsed value failed domain validation."""


class JsonLlmExhaustedError(JsonLlmError):
    """All retry attempts failed."""

    def __init__(
        self, message: str, *, attempts: int, last_error: BaseException | None
    ):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


def execute_llm_task(
    client: OpenAI,
    *,
    model: str,
    messages: list,
    max_completion_tokens: int,
    response_model: type[BaseModel],
    task_kind: str,
    logger: logging.Logger,
    postprocess: Callable[[BaseModel], T],
    fallback: T | None = None,
    fallback_factory: Callable[[BaseException | None], T] | None = None,
    extra_log_fields: dict[str, Any] | None = None,
) -> tuple[T, TokenUsage]:
    """Call Responses structured output, retry on failure, then run ``postprocess``.

    On exhaustion: if ``fallback`` or ``fallback_factory`` is set, return that
    value with empty token usage; otherwise raise :class:`JsonLlmExhaustedError`.
    """
    base_extra: dict[str, Any] = {"task": task_kind, "model": model}
    if extra_log_fields:
        base_extra = {**base_extra, **extra_log_fields}

    def _attempt() -> tuple[T, TokenUsage]:
        try:
            parsed, usage = complete_structured(
                client,
                model=model,
                messages=messages,
                max_completion_tokens=max_completion_tokens,
                response_model=response_model,
            )
        except Exception as e:
            raise JsonLlmApiError(str(e)) from e
        try:
            result = postprocess(parsed)
        except JsonLlmError:
            raise
        except ValueError:
            raise
        except Exception as e:
            raise JsonLlmParseError(str(e)) from e
        return result, usage

    def _on_before_retry(attempt: int, delay: float) -> None:
        logger.warning(
            "json_llm retry %d for %s after %ds",
            attempt,
            task_kind,
            int(delay),
            extra={**base_extra, "attempt": attempt},
        )

    def _on_failed(attempt: int, err: BaseException) -> None:
        logger.warning(
            "json_llm attempt %d failed for %s: %s",
            attempt,
            task_kind,
            err,
            extra={**base_extra, "attempt": attempt},
        )

    success, last_error = retry_with_exponential_backoff(
        _attempt,
        max_attempts=MAX_JSON_LLM_ATTEMPTS,
        on_before_retry=_on_before_retry,
        on_attempt_failed=_on_failed,
    )
    if success is not None:
        return success
    if fallback is not None:
        return fallback, TokenUsage()
    if fallback_factory is not None:
        return fallback_factory(last_error), TokenUsage()
    raise JsonLlmExhaustedError(
        f"json_llm exhausted after {MAX_JSON_LLM_ATTEMPTS} attempts for {task_kind}",
        attempts=MAX_JSON_LLM_ATTEMPTS,
        last_error=last_error,
    ) from last_error


# ---------------------------------------------------------------------------
# Shared LLM postprocess helpers
# ---------------------------------------------------------------------------


def extract_llm_questions(
    items: Iterable,
    *,
    expected_qids: Sequence[str],
    get_qid: Callable[[Any], str],
) -> dict[str, Any]:
    """Normalize QIDs, deduplicate (first wins), and validate completeness.

    Returns ``{normalized_qid: item}`` for all valid items.
    Raises ``ValueError`` if any ``expected_qids`` are missing from the output.
    """
    out: dict[str, Any] = {}
    for item in items:
        try:
            qid = normalize_qid(get_qid(item))
        except ValueError:
            continue
        if qid not in out:
            out[qid] = item
    missing = [q for q in expected_qids if q not in out]
    if missing:
        raise ValueError(f"LLM response missing questions: {missing}")
    return out
