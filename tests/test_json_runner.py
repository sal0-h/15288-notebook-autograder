"""Unit tests for llm.json_runner (mocked complete_structured; no API calls)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from llm.json_runner import (
    MAX_JSON_LLM_ATTEMPTS,
    JsonLlmApiError,
    JsonLlmExhaustedError,
    execute_llm_task,
    extract_llm_questions,
    run_jobs,
)
from token_usage import TokenUsage


class _EchoModel(BaseModel):
    value: int


def _fake_complete(value: int) -> tuple:
    return (_EchoModel(value=value), TokenUsage(1, 2))


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


def test_execute_llm_task_retries_after_postprocess_failure_then_succeeds():
    """First postprocess raises; second attempt succeeds."""
    log = logging.getLogger("test_execute_retry")

    def _postprocess(p):
        if p.value < 0:
            raise ValueError("negative value not allowed")
        return {"value": p.value}

    with patch("llm.json_runner.time.sleep"):
        with patch(
            "llm.json_runner.complete_structured",
            side_effect=[_fake_complete(-1), _fake_complete(7)],
        ):
            out, usage = execute_llm_task(
                MagicMock(),
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "go"}],
                max_completion_tokens=100,
                response_model=_EchoModel,
                task_kind="test_retry",
                logger=log,
                postprocess=_postprocess,
            )
    assert out == {"value": 7}
    assert usage.completion_tokens == 2


def test_execute_llm_task_exhausted_when_api_always_fails():
    log = logging.getLogger("test_execute_exhaust")
    with patch("llm.json_runner.time.sleep"):
        with patch(
            "llm.json_runner.complete_structured",
            side_effect=RuntimeError("network"),
        ):
            with pytest.raises(JsonLlmExhaustedError) as ei:
                execute_llm_task(
                    MagicMock(),
                    model="gpt-4.1-mini",
                    messages=[{"role": "user", "content": "go"}],
                    max_completion_tokens=100,
                    response_model=_EchoModel,
                    task_kind="test_exhaust",
                    logger=log,
                    postprocess=lambda p: {"value": p.value},
                )
    assert ei.value.attempts == MAX_JSON_LLM_ATTEMPTS
    assert isinstance(ei.value.last_error, JsonLlmApiError)
    assert isinstance(ei.value.last_error.__cause__, RuntimeError)


def test_run_jobs_parallel():
    seen = list(run_jobs([1, 2, 3], lambda x: x * 2, max_workers=2))
    assert set(seen) == {2, 4, 6}


def test_run_jobs_sequential():
    seen = list(run_jobs([1, 2, 3], lambda x: x * 2, max_workers=1))
    assert seen == [2, 4, 6]


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


class _QuestionItem:
    def __init__(self, question_id: str, score: float):
        self.question_id = question_id
        self.score = score


class TestExtractLlmQuestions:
    def test_normalizes_and_deduplicates(self):
        items = [
            _QuestionItem("Q1.1", 10),
            _QuestionItem("1.2", 8),
            _QuestionItem("Q1.1", 5),  # duplicate
        ]
        result = extract_llm_questions(
            items,
            expected_qids=["1.1", "1.2"],
            get_qid=lambda x: x.question_id,
        )
        assert set(result.keys()) == {"1.1", "1.2"}
        assert result["1.1"].score == 10  # first wins

    def test_raises_on_missing_qids(self):
        items = [_QuestionItem("1.1", 10)]
        with pytest.raises(ValueError, match="missing questions.*1.2"):
            extract_llm_questions(
                items,
                expected_qids=["1.1", "1.2"],
                get_qid=lambda x: x.question_id,
            )

    def test_skips_invalid_qids(self):
        items = [
            _QuestionItem("bad", 0),
            _QuestionItem("1.1", 10),
        ]
        result = extract_llm_questions(
            items,
            expected_qids=["1.1"],
            get_qid=lambda x: x.question_id,
        )
        assert list(result.keys()) == ["1.1"]
