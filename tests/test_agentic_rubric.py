"""Tests for agentic rubric generation/review. CrewAI runners are monkeypatched."""

import json
from unittest.mock import MagicMock

import pytest

from config_models import RubricEntry, RubricItem, ensure_app_config
from llm.json_runner import LlmContext, load_llm_context
from rubric_generate import (
    GENERATION_FAILED,
    RubricGenJob,
    generate_one_group,
    generate_rubrics,
)
from rubric_review import RubricReviewJob, review_one_group
from token_usage import TokenUsage


def _cfg(crew_type="lean"):
    return ensure_app_config(
        {
            "assignment_name": "T",
            "agentic": {"enabled": True, "crew_type": crew_type},
        }
    )


def _solution():
    return {
        "sections": {
            "1": {
                "questions": {
                    "1.1": {
                        "points": 2,
                        "question_markdown": "Q1.1 do a thing [2 PTS]",
                    },
                    "1.2": {
                        "points": 3,
                        "question_markdown": "Q1.2 do another [3 PTS]",
                    },
                }
            }
        }
    }


def _llm_ctx(config):
    return load_llm_context(config, "rubric_system", client=MagicMock())


class TestGenerateAgentic:
    def test_normalizes_and_returns_same_shape(self, monkeypatch):
        raw = {
            "1.1": {
                "points": 2,
                "items": [
                    {"description": "core idea", "deduction": 1},
                    {"description": "second", "deduction": 1},
                ],
            }
        }
        monkeypatch.setattr(
            "agentic.rubric_crew.run_rubric_generation",
            lambda *a, **k: (raw, TokenUsage(5, 3)),
        )
        cfg = _cfg()
        job = RubricGenJob(
            idx=0,
            group=["1.1"],
            solution_parsed=_solution(),
            config=cfg,
            ctx=_llm_ctx(cfg),
        )
        idx, group, rubrics, usage, had_error = generate_one_group(job)
        assert idx == 0 and group == ["1.1"]
        assert isinstance(rubrics["1.1"], RubricEntry)
        assert rubrics["1.1"].points == 2
        assert sum(i.deduction for i in rubrics["1.1"].items) == pytest.approx(2)
        assert usage.prompt_tokens == 5
        assert had_error is False

    def test_rescales_when_deductions_drift(self, monkeypatch):
        raw = {"1.2": {"points": 3, "items": [{"description": "x", "deduction": 10}]}}
        monkeypatch.setattr(
            "agentic.rubric_crew.run_rubric_generation",
            lambda *a, **k: (raw, TokenUsage()),
        )
        cfg = _cfg()
        job = RubricGenJob(
            idx=0,
            group=["1.2"],
            solution_parsed=_solution(),
            config=cfg,
            ctx=_llm_ctx(cfg),
        )
        _, _, rubrics, _, _ = generate_one_group(job)
        assert sum(i.deduction for i in rubrics["1.2"].items) == pytest.approx(3)

    def test_backfills_missing_question(self, monkeypatch):
        monkeypatch.setattr(
            "agentic.rubric_crew.run_rubric_generation",
            lambda *a, **k: ({}, TokenUsage()),
        )
        cfg = _cfg()
        job = RubricGenJob(
            idx=0,
            group=["1.1"],
            solution_parsed=_solution(),
            config=cfg,
            ctx=_llm_ctx(cfg),
        )
        _, _, rubrics, _, _ = generate_one_group(job)
        assert "1.1" in rubrics
        assert rubrics["1.1"].points == 2

    def test_falls_back_on_exception(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("crew down")

        monkeypatch.setattr("agentic.rubric_crew.run_rubric_generation", boom)
        cfg = _cfg()
        job = RubricGenJob(
            idx=0,
            group=["1.1"],
            solution_parsed=_solution(),
            config=cfg,
            ctx=_llm_ctx(cfg),
        )
        _, _, rubrics, _, had_error = generate_one_group(job)
        assert had_error is True
        assert rubrics["1.1"].items[0].description == GENERATION_FAILED


class TestReviewAgentic:
    def test_preserves_points_and_deductions(self, monkeypatch):
        existing = {
            "1.1": RubricEntry(
                points=2,
                items=[RubricItem(description="old", deduction=2.0)],
            )
        }
        raw = {
            "1.1": {
                "points": 99,
                "items": [{"description": "new wording", "deduction": 5}],
            }
        }
        monkeypatch.setattr(
            "agentic.rubric_crew.run_rubric_review",
            lambda *a, **k: (raw, TokenUsage(), {"1.1": existing["1.1"].model_dump()}),
        )
        cfg = _cfg()
        ctx = _llm_ctx(cfg)
        revised = review_one_group(
            RubricReviewJob(
                group=["1.1"],
                rubrics=existing,
                solution_parsed=_solution(),
                config=cfg,
                ctx=ctx,
            )
        )
        assert revised["1.1"].points == 2
        assert revised["1.1"].items[0].deduction == 2.0
        assert revised["1.1"].items[0].description == "new wording"

    def test_empty_when_no_group_rubrics(self, monkeypatch):
        monkeypatch.setattr(
            "agentic.rubric_crew.run_rubric_review",
            lambda *a, **k: ({}, TokenUsage(), {}),
        )
        cfg = _cfg()
        revised = review_one_group(
            RubricReviewJob(
                group=["1.1"],
                rubrics={},
                solution_parsed=_solution(),
                config=cfg,
                ctx=_llm_ctx(cfg),
            )
        )
        assert revised == {}


class TestDispatch:
    def test_generate_rubrics_uses_agentic_when_enabled(self, monkeypatch, tmp_path):
        sol_path = tmp_path / "solution_parsed.json"
        sol_path.write_text(json.dumps(_solution()), encoding="utf-8")

        cfg = _cfg()
        cfg_dict = cfg.model_dump(mode="python")
        cfg_dict["output_dir"] = str(tmp_path)
        cfg_dict["grading"] = {"question_groups": [["1.1"]]}
        cfg_dict["rubric_review"] = False
        cfg = ensure_app_config(cfg_dict)

        called = {"agentic": False}

        def fake_gen(*a, **k):
            called["agentic"] = True
            return (
                {"1.1": {"points": 2, "items": [{"description": "x", "deduction": 2}]}},
                TokenUsage(),
            )

        monkeypatch.setattr("agentic.rubric_crew.run_rubric_generation", fake_gen)
        generate_rubrics(cfg, client=MagicMock())
        assert called["agentic"] is True

    def test_generate_rubrics_skips_agentic_when_disabled(self, monkeypatch, tmp_path):
        sol_path = tmp_path / "solution_parsed.json"
        sol_path.write_text(json.dumps(_solution()), encoding="utf-8")

        cfg = ensure_app_config(
            {
                "assignment_name": "T",
                "output_dir": str(tmp_path),
                "grading": {"question_groups": [["1.1"]]},
                "rubric_review": False,
                "model": "gpt-4.1-mini",
            }
        )

        called = {"agentic": False}

        def fake_gen(*a, **k):
            called["agentic"] = True
            return ({}, TokenUsage())

        monkeypatch.setattr("agentic.rubric_crew.run_rubric_generation", fake_gen)

        from grading_models import RubricGroupLlmResponse, RubricQuestionLlm

        monkeypatch.setattr(
            "llm.json_runner.complete_structured",
            lambda *a, **k: (
                RubricGroupLlmResponse(
                    questions=[
                        RubricQuestionLlm(
                            question_id="1.1",
                            points=2,
                            items=[RubricItem(description="ok", deduction=2.0)],
                        )
                    ]
                ),
                TokenUsage(1, 1),
            ),
        )
        generate_rubrics(cfg, client=MagicMock())

        assert called["agentic"] is False
