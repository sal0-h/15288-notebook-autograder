"""Tests for load_grade_queue and bulk estimate_grade (no live API)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from batch_grader import load_grade_queue
from config_models import AppConfig, default_config, ensure_app_config
from estimate import estimate_grade
from config_models import get_assignment_output_paths


def _make_solution(qids: list[str]) -> dict:
    sections: dict = {}
    for qid in qids:
        sec, _ = qid.split(".")
        sections.setdefault(sec, {"questions": {}})
        sections[sec]["questions"][qid] = {
            "points": 2,
            "question_markdown": f"Q{qid}",
            "answer_cells": [],
        }
    return {"sections": sections}


def _make_student(name: str, qids: list[str]) -> dict:
    sections: dict = {}
    for qid in qids:
        sec, _ = qid.split(".")
        sections.setdefault(sec, {"questions": {}})
        sections[sec]["questions"][qid] = {
            "points": 2,
            "question_markdown": f"Q{qid}",
            "answer_cells": [{"code": "x=1", "output_text": "", "images": []}],
        }
    return {"sections": sections, "student_name": name}


def _graded_row(name: str, qid: str = "1.1") -> dict:
    return {
        "student_name": name,
        "questions": {
            qid: {
                "score": 2.0,
                "max": 2,
                "feedback": "ok",
                "confidence": "high",
                "requires_review": False,
            }
        },
        "total_score": 2.0,
        "total_max": 2.0,
        "summary_feedback": "",
    }


def _fixture_dirs(tmp_path: Path) -> tuple[AppConfig, Path, Path]:
    out = tmp_path / "out"
    parsed = out / "parsed"
    out.mkdir(parents=True)
    parsed.mkdir(parents=True)
    raw = default_config(
        assignment_name="queue_test",
        output_dir=str(out),
        parsed_dir=str(parsed),
    )
    raw["grading"] = {"question_groups": [["1.1"]]}
    return ensure_app_config(raw), out, parsed


class TestLoadGradeQueue:
    def test_raises_without_solution_parsed(self, tmp_path):
        cfg, out, parsed = _fixture_dirs(tmp_path)
        (parsed / "Alice.json").write_text(
            json.dumps(_make_student("Alice", ["1.1"])), encoding="utf-8"
        )
        with pytest.raises(FileNotFoundError, match="Solution parsed not found"):
            load_grade_queue(cfg)

    def test_all_pending_when_none_graded(self, tmp_path):
        cfg, out, parsed = _fixture_dirs(tmp_path)
        sol = _make_solution(["1.1"])
        (out / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")
        for name in ("Alice", "Bob"):
            (parsed / f"{name}.json").write_text(
                json.dumps(_make_student(name, ["1.1"])), encoding="utf-8"
            )
        gq = load_grade_queue(cfg)
        assert len(gq.to_grade) == 2

    def test_skips_already_graded(self, tmp_path):
        cfg, out, parsed = _fixture_dirs(tmp_path)
        sol = _make_solution(["1.1"])
        (out / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")
        for name in ("Alice", "Bob"):
            (parsed / f"{name}.json").write_text(
                json.dumps(_make_student(name, ["1.1"])), encoding="utf-8"
            )
        (out / "graded_results.json").write_text(
            json.dumps([_graded_row("Alice")], indent=2), encoding="utf-8"
        )
        gq = load_grade_queue(cfg)
        assert len(gq.to_grade) == 1
        assert gq.to_grade[0][1].stem == "Bob"


class TestEstimatePathsInSyncWithBatchGrader:
    def test_paths_match_helper_and_bulk_estimate_succeeds(self, tmp_path):
        cfg, out, parsed = _fixture_dirs(tmp_path)
        paths = get_assignment_output_paths(cfg)
        sol = _make_solution(["1.1"])
        paths.solution_parsed.write_text(json.dumps(sol), encoding="utf-8")
        (parsed / "Alice.json").write_text(
            json.dumps(_make_student("Alice", ["1.1"])), encoding="utf-8"
        )
        gq = load_grade_queue(cfg)
        r = estimate_grade(cfg)
        assert "error" not in r
        assert r["pending_students"] == len(gq.to_grade)


class TestEstimateGradeBulk:
    def test_pending_counts_and_cost_shape(self, tmp_path):
        cfg, out, parsed = _fixture_dirs(tmp_path)
        sol = _make_solution(["1.1"])
        (out / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")
        for name in ("Alice", "Bob", "Carol"):
            (parsed / f"{name}.json").write_text(
                json.dumps(_make_student(name, ["1.1"])), encoding="utf-8"
            )
        (out / "graded_results.json").write_text(
            json.dumps([_graded_row("Alice")], indent=2), encoding="utf-8"
        )
        r = estimate_grade(cfg)
        assert "error" not in r
        assert r["pending_students"] == 2
        assert r["cost_usd"] >= 0
