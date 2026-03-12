"""Tests for export.py: Gradescope JSON format, Excel generation."""

import json
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from export import export_all, export_autograder_zip
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
        assert (
            gs_data["tests"][0]["name"] == "1.1"
        )  # qid as-is (no Q prefix) for Gradescope outline match
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

    def test_grade_only_filters_gradescope_export(self, tmp_path):
        results = [
            {
                "student_name": "Bob",
                "questions": {
                    "8.1": {"score": 2, "max": 2, "feedback": "ok"},
                    "9.1": {"score": 3, "max": 7, "feedback": "partial"},
                    "1.1": {
                        "score": 0,
                        "max": 1,
                        "feedback": "[skipped - not in grade_only]",
                    },
                },
                "total_score": 5,
                "total_max": 10,
                "summary_feedback": "",
            },
        ]
        (tmp_path / "graded_results.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        config = _make_config(tmp_path, tmp_path)
        config["grading"] = {"grade_only": ["8.1", "9.1"]}
        export_all(config)
        gs_data = json.loads((tmp_path / "gradescope" / "Bob.json").read_text())
        names = [t["name"] for t in gs_data["tests"]]
        assert names == ["8.1", "9.1"]
        assert "1.1" not in names

    def test_gradescope_title_mapping(self, tmp_path):
        results = [
            {
                "student_name": "Carol",
                "questions": {"8.1": {"score": 1, "max": 2, "feedback": ""}},
                "total_score": 1,
                "total_max": 2,
                "summary_feedback": "",
            },
        ]
        (tmp_path / "graded_results.json").write_text(
            json.dumps(results, indent=2), encoding="utf-8"
        )
        config = _make_config(tmp_path, tmp_path)
        config["gradescope_title_mapping"] = {"8.1": "Outline Item 5"}
        export_all(config)
        gs_data = json.loads((tmp_path / "gradescope" / "Carol.json").read_text())
        assert gs_data["tests"][0]["name"] == "Outline Item 5"


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
        config = _make_config(tmp_path, tmp_path)
        export_all(config)
        zip_path = export_autograder_zip(config)
        assert zip_path.exists()
        assert zip_path.name == "gradescope_autograder.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "setup.sh" in names
            assert "run_autograder" in names
            assert "results/Alice.json" in names
            # setup.sh and run_autograder should be executable (Unix)
            for name in ("setup.sh", "run_autograder"):
                info = zf.getinfo(name)
                assert (info.external_attr >> 16) & 0o111  # executable bit
                assert info.create_system == 3  # Unix

    def test_raises_if_gradescope_dir_missing(self, tmp_path):
        config = _make_config(tmp_path, tmp_path)
        with pytest.raises(FileNotFoundError, match="Run export first"):
            export_autograder_zip(config)
