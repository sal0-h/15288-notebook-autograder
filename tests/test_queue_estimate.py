"""Tests for load_grade_queue and bulk estimate_grade (no live API)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from batch_grader import load_grade_queue
from config_models import default_config
from estimate import estimate_grade
from utils import get_assignment_output_paths


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


def _fixture_dirs(tmp_path: Path) -> tuple[dict, Path, Path]:
    out = tmp_path / "out"
    parsed = out / "parsed"
    out.mkdir(parents=True)
    parsed.mkdir(parents=True)
    cfg = default_config(
        assignment_name="queue_test",
        output_dir=str(out),
        parsed_dir=str(parsed),
    )
    cfg["grading"] = {"question_groups": [["1.1"]]}
    return cfg, out, parsed


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
        assert len(gq.student_files) == 2
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
    """estimate_grade uses get_assignment_output_paths — same layout as load_grade_queue."""

    def test_paths_match_helper_and_bulk_estimate_succeeds(self, tmp_path):
        cfg, out, parsed = _fixture_dirs(tmp_path)
        paths = get_assignment_output_paths(cfg)
        assert paths.solution_parsed == out / "solution_parsed.json"
        assert paths.parsed_dir == parsed
        sol = _make_solution(["1.1"])
        paths.solution_parsed.write_text(json.dumps(sol), encoding="utf-8")
        (parsed / "Alice.json").write_text(
            json.dumps(_make_student("Alice", ["1.1"])), encoding="utf-8"
        )
        gq = load_grade_queue(cfg)
        assert paths.solution_parsed.exists()
        r = estimate_grade(cfg)
        assert "error" not in r
        assert r["pending_students"] == len(gq.to_grade)


class TestEstimateGradeBulk:
    def test_error_when_no_solution(self, tmp_path):
        cfg, out, parsed = _fixture_dirs(tmp_path)
        (parsed / "Alice.json").write_text(
            json.dumps(_make_student("Alice", ["1.1"])), encoding="utf-8"
        )
        r = estimate_grade(cfg)
        assert "error" in r
        assert "parse" in r["error"].lower()

    def test_error_when_no_parsed_dir(self, tmp_path):
        cfg, out, _parsed = _fixture_dirs(tmp_path)
        sol = _make_solution(["1.1"])
        (out / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")
        shutil.rmtree(out / "parsed")
        r = estimate_grade(cfg)
        assert "error" in r

    def test_error_when_no_student_jsons(self, tmp_path):
        cfg, out, parsed = _fixture_dirs(tmp_path)
        sol = _make_solution(["1.1"])
        (out / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")
        assert list(parsed.glob("*.json")) == []
        r = estimate_grade(cfg)
        assert "error" in r

    def test_all_graded_note_and_zero_cost(self, tmp_path):
        cfg, out, parsed = _fixture_dirs(tmp_path)
        sol = _make_solution(["1.1"])
        (out / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")
        for name in ("Alice", "Bob"):
            (parsed / f"{name}.json").write_text(
                json.dumps(_make_student(name, ["1.1"])), encoding="utf-8"
            )
        (out / "graded_results.json").write_text(
            json.dumps([_graded_row("Alice"), _graded_row("Bob")], indent=2),
            encoding="utf-8",
        )
        r = estimate_grade(cfg)
        assert r.get("error") is None
        assert r.get("note") == "No students pending grading (everyone already graded)."
        assert r["pending_students"] == 0
        assert r["total_parsed"] == 2
        assert r["skipped_students"] == 2
        assert r["cost_usd"] == 0.0

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
        assert r["total_parsed"] == 3
        assert r["skipped_students"] == 1
        assert r["num_students"] == 2
        assert r["cost_usd"] >= 0


class TestDoctorScript:
    """Smoke test: doctor loads real config resolution (output/{name}/ paths)."""

    def test_exits_zero_with_artifacts(self, tmp_path):
        assignment = "DocSmoke"
        cfg_dir = tmp_path / "output" / assignment
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "config.yaml"
        cfg_path.write_text(
            f"""
assignment_name: {assignment}
output_dir: output
parsing:
  section_regex: '[0-9]+'
  question_regex: 'Q[0-9]+'
grading:
  question_groups: [["1.1"]]
""",
            encoding="utf-8",
        )
        sol = _make_solution(["1.1"])
        (cfg_dir / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")
        parsed = cfg_dir / "parsed"
        parsed.mkdir()
        (parsed / "Alice.json").write_text(
            json.dumps(_make_student("Alice", ["1.1"])), encoding="utf-8"
        )
        repo_root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            [
                sys.executable,
                str(repo_root / "doctor.py"),
                "--config",
                str(cfg_path.resolve()),
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Grade queue:" in proc.stdout

    def test_exits_nonzero_without_solution(self, tmp_path):
        assignment = "DocBad"
        cfg_dir = tmp_path / "output" / assignment
        cfg_dir.mkdir(parents=True)
        cfg_path = cfg_dir / "config.yaml"
        cfg_path.write_text(
            f"""
assignment_name: {assignment}
output_dir: output
parsing:
  section_regex: '[0-9]+'
  question_regex: 'Q[0-9]+'
grading:
  question_groups: [["1.1"]]
""",
            encoding="utf-8",
        )
        repo_root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            [
                sys.executable,
                str(repo_root / "doctor.py"),
                "--config",
                str(cfg_path.resolve()),
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 1
        assert "solution_parsed" in proc.stdout.lower() or "Missing" in proc.stdout
