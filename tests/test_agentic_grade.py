"""Tests for agentic grading dispatch. CrewAI grading runner is monkeypatched."""

from unittest.mock import MagicMock

from config_models import ensure_app_config
from grade import grade_group, grade_group_agentic
from grading_models import GRADING_FAILED, QuestionGrade
from llm.json_runner import load_llm_context
from token_usage import TokenUsage


def _cfg(crew_type="lean"):
    return ensure_app_config(
        {
            "assignment_name": "T",
            "agentic": {"enabled": True, "crew_type": crew_type},
            "model": "gpt-4.1-mini",
        }
    )


def _solution():
    return {
        "sections": {
            "1": {
                "questions": {
                    "1.1": {"points": 2, "question_markdown": "Q1.1 [2 PTS]"},
                }
            }
        }
    }


def _student():
    return {
        "student_name": "Alice",
        "sections": {
            "1": {"questions": {"1.1": {"points": 2, "answer_code_concat": "x = 1"}}}
        },
    }


def _ctx(cfg):
    return load_llm_context(cfg, "grade_system", client=MagicMock())


class TestGradeGroupAgentic:
    def test_returns_validated_grading_response(self, monkeypatch):
        items = [
            QuestionGrade(
                question_id="1.1",
                score=2,
                feedback="ok",
                confidence="high",
            )
        ]
        monkeypatch.setattr(
            "agentic.grading_crew.run_grading",
            lambda *a, **k: (items, {"1.1": 2}, TokenUsage(5, 3), {}),
        )
        grade_items, qid_to_max, usage, inj = grade_group_agentic(
            ["1.1"], _solution(), _student(), _cfg(), _ctx(_cfg()), student_name="Alice"
        )
        assert all(isinstance(g, QuestionGrade) for g in grade_items)
        assert grade_items[0].score == 2
        assert qid_to_max == {"1.1": 2}
        assert usage.prompt_tokens == 5
        assert inj == {}

    def test_falls_back_on_exception(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("crew down")

        monkeypatch.setattr("agentic.grading_crew.run_grading", boom)
        grade_items, qid_to_max, usage, inj = grade_group_agentic(
            ["1.1"], _solution(), _student(), _cfg(), _ctx(_cfg())
        )
        assert grade_items[0].score == 0.0
        assert grade_items[0].feedback == GRADING_FAILED
        assert grade_items[0].requires_review is True
        assert qid_to_max == {"1.1": 2}
        assert not usage.has_tokens()
        assert inj == {}


class TestDispatch:
    def test_grade_group_delegates_when_agentic_enabled(self, monkeypatch):
        called = {"agentic": False}

        def fake(*a, **k):
            called["agentic"] = True
            return ([], {}, TokenUsage(), {})

        monkeypatch.setattr("grade.grade_group_agentic", fake)
        cfg = _cfg()
        grade_group(["1.1"], _solution(), _student(), cfg, _ctx(cfg))
        assert called["agentic"] is True

    def test_grade_group_skips_agentic_when_disabled(self, monkeypatch):
        called = {"agentic": False}

        def fake(*a, **k):
            called["agentic"] = True
            return ([], {}, TokenUsage(), {})

        monkeypatch.setattr("grade.grade_group_agentic", fake)

        cfg = ensure_app_config({"assignment_name": "T", "model": "gpt-4.1-mini"})

        from grading_models import GradingLlmResponse

        monkeypatch.setattr(
            "llm.json_runner.complete_structured",
            lambda *a, **k: (
                GradingLlmResponse(
                    grades=[
                        QuestionGrade(
                            question_id="1.1",
                            score=2,
                            feedback="ok",
                            confidence="high",
                        )
                    ]
                ),
                TokenUsage(1, 1),
            ),
        )
        grade_group(["1.1"], _solution(), _student(), cfg, _ctx(cfg))
        assert called["agentic"] is False
