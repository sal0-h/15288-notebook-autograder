"""Tests for export.py: Gradescope JSON format, Excel generation."""

import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from config_models import ensure_app_config
from export import export_all, export_autograder_zip


def _make_config(tmp_path: Path, output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "assignment_name": "Test",
    }


def _write_parsed_student(tmp_path: Path, student_name: str, qids: list[str]) -> None:
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    questions = {
        qid: {
            "points": 0,
            "question_markdown": "",
            "answer_cells": [],
            "answer_code_concat": "",
            "answer_text_concat": "",
            "answer_markdown_concat": "",
        }
        for qid in qids
    }
    parsed = {
        "sections": {"1": {"overview_markdown": "", "questions": questions}},
        "duplicate_qids": [],
        "student_name": student_name,
    }
    (parsed_dir / f"{student_name}.json").write_text(
        json.dumps(parsed, indent=2), encoding="utf-8"
    )


class TestExportAll:
    def test_missing_graded_results_raises(self, tmp_path):
        config = _make_config(tmp_path, tmp_path)
        with pytest.raises(FileNotFoundError, match="Graded results not found"):
            export_all(ensure_app_config(config))

    def test_empty_results(self, tmp_path):
        graded_path = tmp_path / "graded_results.json"
        graded_path.write_text("[]", encoding="utf-8")
        config = _make_config(tmp_path, tmp_path)
        summary = export_all(ensure_app_config(config))
        assert summary["students"] == 0
        assert summary["excel_path"] == ""

    def test_gradescope_json_format(self, tmp_path):
        results = [
            {
                "student_name": "Alice",
                "questions": {"1.1": {"score": 2, "max": 2, "feedback": "ok"}},
                "total_score": 2,
                "total_max": 2,
                "summary_feedback": "",
            },
        ]
        (tmp_path / "graded_results.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        _write_parsed_student(tmp_path, "Alice", ["1.1"])
        config = _make_config(tmp_path, tmp_path)
        summary = export_all(ensure_app_config(config))
        assert summary["students"] == 1
        gs_data = json.loads((tmp_path / "gradescope" / "Alice.json").read_text())
        assert gs_data["tests"][0]["name"] == "1.1"
        assert gs_data["tests"][-1]["name"] == "Notebook Format Lint"

    def test_excel_columns(self, tmp_path):
        results = [
            {
                "student_name": "Alice",
                "questions": {"1.1": {"score": 1, "max": 2, "feedback": ""}},
                "total_score": 1,
                "total_max": 2,
                "summary_feedback": "Q1.1: -1",
            },
        ]
        (tmp_path / "graded_results.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        _write_parsed_student(tmp_path, "Alice", ["1.1"])
        export_all(ensure_app_config(_make_config(tmp_path, tmp_path)))
        df = pd.read_excel(tmp_path / "Final_Grades.xlsx")
        assert "student_name" in df.columns
        assert df.iloc[0]["student_name"] == "Alice"


class TestExportAutograderZip:
    def test_creates_zip_with_required_files(self, tmp_path):
        results = [
            {
                "student_name": "Alice",
                "questions": {"1.1": {"score": 2, "max": 2, "feedback": "ok"}},
                "total_score": 2,
                "total_max": 2,
                "summary_feedback": "",
            },
        ]
        (tmp_path / "graded_results.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        export_all(ensure_app_config(_make_config(tmp_path, tmp_path)))
        zip_path = export_autograder_zip(
            ensure_app_config(_make_config(tmp_path, tmp_path))
        )
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "setup.sh" in names
            assert "run_autograder" in names
            assert "results/Alice.json" in names

    def test_raises_if_gradescope_dir_missing(self, tmp_path):
        config = _make_config(tmp_path, tmp_path)
        with pytest.raises(FileNotFoundError, match="Run export first"):
            export_autograder_zip(ensure_app_config(config))
