"""Tests for batch_grader: queue loading and resume logic."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from config_models import ensure_app_config, DEFAULT_MODEL
from batch_grader import load_grade_queue


def _write_solution(output_dir: Path) -> None:
    solution = {
        "sections": {"1": {"questions": {"1.1": {"points": 5, "question_markdown": "Q1.1", "answer_code_concat": "", "answer_text_concat": "", "answer_markdown_concat": "", "answer_cells": []}}}}
    }
    (output_dir / "solution_parsed.json").write_text(json.dumps(solution))


def _write_student(parsed_dir: Path, name: str) -> None:
    parsed_dir.mkdir(parents=True, exist_ok=True)
    data = {"student_name": name, "sections": {"1": {"questions": {"1.1": {"points": 5, "question_markdown": "Q", "answer_code_concat": "x=1", "answer_text_concat": "", "answer_markdown_concat": "", "answer_cells": []}}}}}
    (parsed_dir / f"{name}.json").write_text(json.dumps(data))


def _cfg(tmp_path: Path) -> dict:
    output_dir = tmp_path / "output" / "test"
    output_dir.mkdir(parents=True)
    return {
        "assignment_name": "test",
        "model": DEFAULT_MODEL,
        "output_dir": str(output_dir),
        "submissions_dir": str(output_dir / "submissions"),
        "parsed_dir": str(output_dir / "parsed"),
        "grading": {"question_groups": [["1.1"]]},
        "rubrics": {},
        "max_prompt_tokens": 80000,
        "max_completion_tokens": 4096,
    }


def test_load_grade_queue_all_pending(tmp_path):
    """All students should be in to_grade when no results exist."""
    cfg_dict = _cfg(tmp_path)
    cfg = ensure_app_config(cfg_dict)
    output_dir = Path(cfg.output_dir)
    parsed_dir = Path(cfg.parsed_dir)
    _write_solution(output_dir)
    _write_student(parsed_dir, "Alice")
    _write_student(parsed_dir, "Bob")

    gq = load_grade_queue(cfg)
    assert len(gq.to_grade) == 2
    assert gq.solution_parsed is not None


def test_load_grade_queue_skips_already_graded(tmp_path):
    """Students already in graded_results.json should be skipped."""
    cfg_dict = _cfg(tmp_path)
    cfg = ensure_app_config(cfg_dict)
    output_dir = Path(cfg.output_dir)
    parsed_dir = Path(cfg.parsed_dir)
    _write_solution(output_dir)
    _write_student(parsed_dir, "Alice")
    _write_student(parsed_dir, "Bob")

    # Write existing results for Alice
    results = [{"student_name": "Alice", "questions": {"1.1": {"score": 5, "max": 5, "feedback": "ok"}}, "total_score": 5, "total_max": 5}]
    (output_dir / "graded_results.json").write_text(json.dumps(results))

    gq = load_grade_queue(cfg)
    assert len(gq.to_grade) == 1
    assert gq.to_grade[0][1].stem == "Bob"


def test_load_grade_queue_empty_when_no_parsed(tmp_path):
    """Queue should be empty when no parsed students exist."""
    cfg_dict = _cfg(tmp_path)
    cfg = ensure_app_config(cfg_dict)
    output_dir = Path(cfg.output_dir)
    _write_solution(output_dir)

    gq = load_grade_queue(cfg)
    assert len(gq.to_grade) == 0
