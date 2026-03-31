"""Unit tests for llm.json_runner (mocked complete_structured; no API calls)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from llm.json_runner import (
    MAX_JSON_LLM_ATTEMPTS,
    JsonLlmApiError,
    JsonLlmExhaustedError,
    execute_llm_task,
    run_json_llm,
    run_parallel_map,
)
from results_models import TokenUsage


class _EchoModel(BaseModel):
    value: int


@dataclass(frozen=True)
class _EchoSpec:
    """Minimal spec: validate the parsed model and echo it as a dict."""

    _model: str
    _max_tokens: int
    _messages: tuple[dict, ...]
    _task: str = "test_echo"

    @property
    def model(self) -> str:
        return self._model

    @property
    def max_completion_tokens(self) -> int:
        return self._max_tokens

    @property
    def task_kind(self) -> str:
        return self._task

    def llm_messages(self) -> list:
        return list(self._messages)

    def response_model(self) -> type[BaseModel]:
        return _EchoModel

    def validate(self, parsed: BaseModel) -> dict:
        assert isinstance(parsed, _EchoModel)
        if parsed.value < 0:
            raise ValueError("negative value not allowed")
        return {"value": parsed.value}


def _fake_complete(value: int) -> tuple:
    return (_EchoModel(value=value), TokenUsage(1, 2))


def test_run_json_llm_success_first_try():
    spec = _EchoSpec(
        _model="gpt-4.1-mini",
        _max_tokens=100,
        _messages=({"role": "user", "content": "go"},),
    )
    log = logging.getLogger("test_json_runner")
    with patch("llm.json_runner.complete_structured", return_value=_fake_complete(42)):
        out, usage = run_json_llm(spec, MagicMock(), logger=log)
    assert out == {"value": 42}
    assert usage.prompt_tokens == 1 and usage.completion_tokens == 2


def test_run_json_llm_retries_after_validate_failure_then_succeeds():
    """First validate raises; second attempt succeeds."""
    spec = _EchoSpec(
        _model="gpt-4.1-mini",
        _max_tokens=100,
        _messages=({"role": "user", "content": "go"},),
    )
    log = logging.getLogger("test_json_runner_retry")
    with patch("llm.json_runner.time.sleep"):
        with patch(
            "llm.json_runner.complete_structured",
            side_effect=[_fake_complete(-1), _fake_complete(7)],
        ):
            out, usage = run_json_llm(spec, MagicMock(), logger=log)
    assert out == {"value": 7}
    assert usage.completion_tokens == 2


def test_run_json_llm_exhausted_when_api_always_fails():
    spec = _EchoSpec(
        _model="gpt-4.1-mini",
        _max_tokens=100,
        _messages=({"role": "user", "content": "go"},),
    )
    log = logging.getLogger("test_json_runner_exhaust")
    with patch("llm.json_runner.time.sleep"):
        with patch(
            "llm.json_runner.complete_structured",
            side_effect=RuntimeError("network"),
        ):
            with pytest.raises(JsonLlmExhaustedError) as ei:
                run_json_llm(spec, MagicMock(), logger=log)
    assert ei.value.attempts == MAX_JSON_LLM_ATTEMPTS
    assert isinstance(ei.value.last_error, JsonLlmApiError)
    assert isinstance(ei.value.last_error.__cause__, RuntimeError)


def test_run_parallel_map_matches_unordered_fanout():
    seen = list(run_parallel_map([1, 2, 3], lambda x: x * 2, max_workers=2))
    assert set(seen) == {2, 4, 6}


def test_execute_llm_task_fallback_factory_on_exhaustion():
    log = logging.getLogger("test_execute_fallback_factory")
    with patch("llm.json_runner.time.sleep"):
        with patch(
            "llm.json_runner.complete_structured",
            side_effect=RuntimeError("network"),
        ):
            out, usage = execute_llm_task(
                MagicMock(),
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "go"}],
                max_completion_tokens=100,
                response_model=_EchoModel,
                task_kind="test_fb",
                logger=log,
                postprocess=lambda p: {"value": p.value},
                fallback_factory=lambda e: {"value": -1},
            )
    assert out == {"value": -1}
    assert usage.prompt_tokens == 0 and usage.completion_tokens == 0


def test_execute_llm_task_static_fallback_on_exhaustion():
    log = logging.getLogger("test_execute_fallback_static")
    with patch("llm.json_runner.time.sleep"):
        with patch(
            "llm.json_runner.complete_structured",
            side_effect=RuntimeError("network"),
        ):
            out, usage = execute_llm_task(
                MagicMock(),
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "go"}],
                max_completion_tokens=100,
                response_model=_EchoModel,
                task_kind="test_fb2",
                logger=log,
                postprocess=lambda p: {"value": p.value},
                fallback={"value": 99},
            )
    assert out == {"value": 99}
    assert usage.total_tokens == 0


def test_execute_llm_task_success():
    log = logging.getLogger("test_execute_ok")
    with patch("llm.json_runner.complete_structured", return_value=_fake_complete(3)):
        out, usage = execute_llm_task(
            MagicMock(),
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": "go"}],
            max_completion_tokens=100,
            response_model=_EchoModel,
            task_kind="test_ok",
            logger=log,
            postprocess=lambda p: {"value": p.value},
        )
    assert out == {"value": 3}
    assert usage.completion_tokens == 2
