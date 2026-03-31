"""Unified structured-output Responses API + retry for all LLM tasks."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Protocol, TypeVar, cast, overload

from openai import OpenAI
from pydantic import BaseModel

from results_models import TokenUsage
from utils import temperature_for_model

T_out = TypeVar("T_out", covariant=True)
_T = TypeVar("_T")
_R = TypeVar("_R")
T = TypeVar("T")

# Application-level retries when validation fails.
MAX_VALIDATION_RETRIES = 2
MAX_JSON_LLM_ATTEMPTS = MAX_VALIDATION_RETRIES + 1


def retry_with_exponential_backoff(
    operation: Callable[[], T],
    *,
    max_attempts: int,
    on_before_retry: Callable[[int, float], None] | None = None,
    on_attempt_failed: Callable[[int, BaseException], None] | None = None,
) -> tuple[T | None, BaseException | None]:
    """Run ``operation`` up to ``max_attempts`` times.

    Before attempts 1 .. max_attempts-1, sleep ``2**attempt`` seconds.
    Returns ``(result, None)`` on success, or ``(None, last_error)`` if every
    attempt raises.
    """
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


def iter_unordered_parallel_results(
    items: Iterable[_T],
    worker: Callable[[_T], _R],
    *,
    max_workers: int,
) -> Iterator[_R]:
    """Run ``worker(item)`` for each item; yield results as tasks finish (arbitrary order)."""
    work = list(items)
    if not work:
        return
    workers = max(1, int(max_workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
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
    """Run structured outputs via Responses API; return ``(parsed_model, token_usage)``.

    Uses ``client.responses.parse`` with ``text_format`` (Pydantic). Requires a
    model that supports Structured Outputs.
    """
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


class JsonLlmSpec(Protocol[T_out]):
    """Structural type for :func:`run_json_llm` (implemented by frozen dataclasses)."""

    @property
    def model(self) -> str: ...

    @property
    def max_completion_tokens(self) -> int: ...

    @property
    def task_kind(self) -> str: ...

    def llm_messages(self) -> list: ...

    def response_model(self) -> type[BaseModel]: ...

    def validate(self, parsed: BaseModel) -> T_out: ...


def _spec_log_extra(spec: JsonLlmSpec[Any]) -> dict[str, Any]:
    fn = getattr(spec, "extra_log_fields", None)
    if callable(fn):
        out = fn()
        if isinstance(out, dict):
            return cast(dict[str, Any], out)
    return {}


def _resolve_fallback_value(
    *,
    fallback: T | None,
    fallback_factory: Callable[[BaseException | None], T] | None,
    last_error: BaseException | None,
) -> T:
    if fallback_factory is not None:
        if fallback is not None:
            raise ValueError("Pass only one of fallback or fallback_factory")
        return fallback_factory(last_error)
    if fallback is not None:
        return fallback
    raise ValueError("internal: fallback resolution without fallback")


@overload
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
    fallback: None = None,
    fallback_factory: None = None,
    extra_log_fields: dict[str, Any] | None = None,
) -> tuple[T, TokenUsage]: ...


@overload
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
    fallback: T,
    fallback_factory: None = None,
    extra_log_fields: dict[str, Any] | None = None,
) -> tuple[T, TokenUsage]: ...


@overload
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
    fallback: None = None,
    fallback_factory: Callable[[BaseException | None], T] = ...,
    extra_log_fields: dict[str, Any] | None = None,
) -> tuple[T, TokenUsage]: ...


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
        extra = {**base_extra, "attempt": attempt}
        logger.warning(
            "json_llm retry %d for %s after %ds",
            attempt,
            task_kind,
            int(delay),
            extra=extra,
        )

    def _on_failed(attempt: int, err: BaseException) -> None:
        extra = {**base_extra, "attempt": attempt}
        logger.warning(
            "json_llm attempt %d failed for %s: %s",
            attempt,
            task_kind,
            err,
            extra=extra,
        )

    success, last_error = retry_with_exponential_backoff(
        _attempt,
        max_attempts=MAX_JSON_LLM_ATTEMPTS,
        on_before_retry=_on_before_retry,
        on_attempt_failed=_on_failed,
    )
    if success is not None:
        return success
    if fallback is not None or fallback_factory is not None:
        fb = _resolve_fallback_value(
            fallback=fallback,
            fallback_factory=fallback_factory,
            last_error=last_error,
        )
        return fb, TokenUsage()
    raise JsonLlmExhaustedError(
        f"json_llm exhausted after {MAX_JSON_LLM_ATTEMPTS} attempts for {task_kind}",
        attempts=MAX_JSON_LLM_ATTEMPTS,
        last_error=last_error,
    ) from last_error


def run_json_llm(
    spec: JsonLlmSpec[T_out],
    client: OpenAI,
    *,
    logger: logging.Logger,
) -> tuple[T_out, TokenUsage]:
    """Run structured output with retries; ``spec`` supplies messages and validation."""

    return execute_llm_task(
        client,
        model=spec.model,
        messages=spec.llm_messages(),
        max_completion_tokens=spec.max_completion_tokens,
        response_model=spec.response_model(),
        task_kind=spec.task_kind,
        logger=logger,
        postprocess=spec.validate,
        extra_log_fields=_spec_log_extra(spec),
    )


def run_parallel_map(
    items: Iterable[_T],
    worker: Callable[[_T], _R],
    *,
    max_workers: int,
) -> Iterator[_R]:
    """Single entry for parallel LLM workers (wraps :func:`iter_unordered_parallel_results`)."""
    return iter_unordered_parallel_results(items, worker, max_workers=max_workers)
