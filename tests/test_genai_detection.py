"""Tests for optional GenAI suspicion pass (separate from grading scores)."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from config_models import default_config, ensure_app_config
from genai_detection import run_genai_detection
from grading_models import GenaiLlmResponse, GenaiQuestionResult
from token_usage import TokenUsage


def _minimal_parsed_student(qid: str = "1.1") -> dict:
    return {
        "sections": {
            "1": {
                "questions": {
                    qid: {
                        "points": 5,
                        "question_markdown": "Compute X.",
                        "answer_markdown_concat": "The result is 2.",
                        "answer_code_concat": "x = 1 + 1",
                    }
                }
            }
        }
    }


def test_run_genai_detection_merges_flags_preserves_scores(tmp_path: Path):
    assign_root = tmp_path / "MyAssign"
    assign_root.mkdir()
    (assign_root / "parsed").mkdir()
    graded_path = assign_root / "graded_results.json"

    graded = [
        {
            "student_name": "Alice",
            "questions": {
                "1.1": {
                    "score": 4.0,
                    "max": 5,
                    "feedback": "Good.",
                    "confidence": "high",
                    "requires_review": False,
                }
            },
            "total_score": 4.0,
            "total_max": 5.0,
        }
    ]
    graded_path.write_text(json.dumps(graded), encoding="utf-8")
    (assign_root / "parsed" / "Alice.json").write_text(
        json.dumps(_minimal_parsed_student()), encoding="utf-8"
    )

    base = default_config("MyAssign")
    base["output_dir"] = str(assign_root)
    base["parsed_dir"] = str(assign_root / "parsed")
    base["submissions_dir"] = str(assign_root / "submissions")
    cfg = ensure_app_config(base)

    fake_parsed = GenaiLlmResponse(
        results=[
            GenaiQuestionResult(
                question_id="1.1", suspicious_genai=True, note="Templated prose"
            )
        ]
    )
    with patch(
        "llm.json_runner.complete_structured",
        return_value=(fake_parsed, TokenUsage(10, 20)),
    ):
        summary = run_genai_detection(cfg)

    assert summary["students_processed"] == 1
    assert summary["questions_flagged"] == 1

    data = json.loads(graded_path.read_text(encoding="utf-8"))
    q = data[0]["questions"]["1.1"]
    assert q["score"] == 4.0
    assert q["feedback"] == "Good."
    assert q["suspicious_genai"] is True
    assert "Templated" in (q.get("suspicious_genai_note") or "")


def test_run_genai_detection_skips_when_no_graded_file(tmp_path: Path):
    assign_root = tmp_path / "Empty"
    assign_root.mkdir()
    (assign_root / "parsed").mkdir()
    base = default_config("Empty")
    base["output_dir"] = str(assign_root)
    base["parsed_dir"] = str(assign_root / "parsed")
    base["submissions_dir"] = str(assign_root / "submissions")
    cfg = ensure_app_config(base)

    with pytest.raises(FileNotFoundError):
        run_genai_detection(cfg)


def test_run_genai_detection_parallel_workers(tmp_path: Path):
    assign_root = tmp_path / "ParallelAssign"
    assign_root.mkdir()
    (assign_root / "parsed").mkdir()
    graded_path = assign_root / "graded_results.json"

    graded = [
        {
            "student_name": "Alice",
            "questions": {
                "1.1": {
                    "score": 4.0,
                    "max": 5,
                    "feedback": "Good.",
                    "confidence": "high",
                    "requires_review": False,
                }
            },
            "total_score": 4.0,
            "total_max": 5.0,
        },
        {
            "student_name": "Bob",
            "questions": {
                "1.1": {
                    "score": 5.0,
                    "max": 5,
                    "feedback": "Great.",
                    "confidence": "high",
                    "requires_review": False,
                }
            },
            "total_score": 5.0,
            "total_max": 5.0,
        },
    ]
    graded_path.write_text(json.dumps(graded), encoding="utf-8")
    (assign_root / "parsed" / "Alice.json").write_text(
        json.dumps(_minimal_parsed_student()), encoding="utf-8"
    )
    (assign_root / "parsed" / "Bob.json").write_text(
        json.dumps(_minimal_parsed_student()), encoding="utf-8"
    )

    base = default_config("ParallelAssign")
    base["output_dir"] = str(assign_root)
    base["parsed_dir"] = str(assign_root / "parsed")
    base["submissions_dir"] = str(assign_root / "submissions")
    base["workers"] = 2
    cfg = ensure_app_config(base)

    fake_parsed = GenaiLlmResponse(
        results=[
            GenaiQuestionResult(question_id="1.1", suspicious_genai=False, note="")
        ]
    )
    with patch(
        "llm.json_runner.complete_structured",
        return_value=(fake_parsed, TokenUsage(10, 20)),
    ) as mock_cs:
        summary = run_genai_detection(cfg)

    assert summary["students_processed"] == 2
    assert summary["questions_flagged"] == 0
    assert mock_cs.call_count == 2
