"""Tests for export.py: Gradescope JSON format, Excel generation."""

import json
from pathlib import Path

import pandas as pd
import pytest

from export import export_all
from parse_notebook import _sort_key_qid
from utils import load_config


def _make_config(tmp_path: Path, output_dir: Path) -> dict:
    return {
        "output_dir": str(output_dir),
        "assignment_name": "Test",
    }


class TestExportAll:
    def test_missing_graded_results_raises(self, tmp_path):
        config = _make_config(tmp_path, tmp_path)
        with pytest.raises(FileNotFoundError, match="Graded results not found"):
            export_all(config)

    def test_empty_results(self, tmp_path):
        graded_path = tmp_path / "graded_results.json"
        graded_path.write_text("[]", encoding="utf-8")
        config = _make_config(tmp_path, tmp_path)
        summary = export_all(config)
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
        graded_path = tmp_path / "graded_results.json"
        graded_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        config = _make_config(tmp_path, tmp_path)
        summary = export_all(config)
        assert summary["students"] == 1
        gs_path = tmp_path / "gradescope" / "Alice.json"
        assert gs_path.exists()
        gs_data = json.loads(gs_path.read_text(encoding="utf-8"))
        assert "tests" in gs_data
        assert len(gs_data["tests"]) == 1
        assert gs_data["tests"][0]["name"] == "Q1.1"
        assert gs_data["tests"][0]["score"] == 2
        assert gs_data["tests"][0]["max_score"] == 2

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
        graded_path = tmp_path / "graded_results.json"
        graded_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        config = _make_config(tmp_path, tmp_path)
        summary = export_all(config)
        excel_path = tmp_path / "Final_Grades.xlsx"
        assert excel_path.exists()
        df = pd.read_excel(excel_path)
        assert "student_name" in df.columns
        assert "total_score" in df.columns
        assert "Q1.1" in df.columns
        assert "summary_feedback" in df.columns
        assert df.iloc[0]["student_name"] == "Alice"
        assert df.iloc[0]["total_score"] == 1

    def test_filename_sanitization_fallback(self, tmp_path):
        results = [
            {
                "student_name": "///",
                "questions": {"1.1": {"score": 0, "max": 1, "feedback": ""}},
                "total_score": 0,
                "total_max": 1,
                "summary_feedback": "",
            },
        ]
        graded_path = tmp_path / "graded_results.json"
        graded_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        config = _make_config(tmp_path, tmp_path)
        export_all(config)
        gs_dir = tmp_path / "gradescope"
        files = list(gs_dir.glob("*.json"))
        assert len(files) == 1
        assert "unknown_student" in files[0].name
