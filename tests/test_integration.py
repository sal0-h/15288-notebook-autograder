"""Integration smoke test: parse + grade (mocked) + export pipeline."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config_models import DEFAULT_MODEL, ensure_app_config
from grading_models import GradingLlmResponse, QuestionGrade
from parse_notebook import get_all_question_ids, parse_all_students, parse_notebook
from export import export_all
from grade import grade_student
from results_models import TokenUsage


def _make_notebook(cells: list[dict]) -> dict:
    return {"cells": cells, "nbformat": 4, "metadata": {}}


def _md_cell(source: str) -> dict:
    return {"cell_type": "markdown", "source": [source], "metadata": {}}


def _code_cell(source: str) -> dict:
    return {"cell_type": "code", "source": [source], "outputs": [], "metadata": {}}


def _minimal_notebook_with_question() -> dict:
    return _make_notebook(
        [
            _md_cell("# <font color='red'>1 Section</font>"),
            _md_cell("- Q1.1 <font color='blue'>[2 PTS] Write code</font>"),
            _code_cell("x = 42"),
        ]
    )


class TestIntegrationPipeline:
    """Smoke test: parse -> grade (mocked) -> export produces expected files."""

    def test_parse_grade_export_pipeline(self, tmp_path):
        """Run minimal pipeline with mocked LLM; verify output files exist."""
        # Setup dirs
        submissions_dir = tmp_path / "submissions"
        parsed_dir = tmp_path / "parsed"
        output_dir = tmp_path / "output"
        submissions_dir.mkdir()
        parsed_dir.mkdir()
        output_dir.mkdir()

        # Write solution and student notebooks
        sol_path = tmp_path / "solution.ipynb"
        sol_path.write_text(json.dumps(_minimal_notebook_with_question()))
        stu_path = submissions_dir / "Alice.ipynb"
        stu_path.write_text(json.dumps(_minimal_notebook_with_question()))

        config = {
            "assignment_name": "SmokeTest",
            "model": DEFAULT_MODEL,
            "solution_notebook": str(sol_path),
            "output_dir": str(output_dir),
            "submissions_dir": str(submissions_dir),
            "parsed_dir": str(parsed_dir),
            "parsing": {
                "section_regex": r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
                "question_regex": r"(?i)^\s*-\s*Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
                "keep_images": True,
            },
            "grading": {"question_groups": [["1.1"]], "grade_only": None},
            "rubrics": {},
        }
        cfg = ensure_app_config(config)

        # Step 1: Parse
        solution_parsed, report = parse_all_students(cfg)
        assert solution_parsed is not None
        assert "1.1" in get_all_question_ids(solution_parsed)
        assert (parsed_dir / "Alice.json").exists()

        # Step 2: Grade (mock LLM)
        student_parsed = json.loads((parsed_dir / "Alice.json").read_text())
        student_parsed["student_name"] = "Alice"
        fake_parsed = GradingLlmResponse(
            grades=[QuestionGrade(question_id="1.1", score=2.0, feedback="ok")]
        )
        with patch(
            "llm.json_runner.complete_structured",
            return_value=(fake_parsed, TokenUsage(100, 50)),
        ):
            result = grade_student(
                student_parsed, solution_parsed, cfg, client=MagicMock()
            )
        assert result["total_score"] == 2
        assert result["total_max"] == 2

        # Write graded_results for export
        (output_dir / "graded_results.json").write_text(json.dumps([result], indent=2))

        # Step 3: Export
        summary = export_all(cfg)
        assert summary["students"] == 1
        assert (output_dir / "gradescope" / "Alice.json").exists()
        assert (output_dir / "Final_Grades.xlsx").exists()

        # Verify Gradescope JSON structure
        gs = json.loads((output_dir / "gradescope" / "Alice.json").read_text())
        assert "tests" in gs
        assert len(gs["tests"]) >= 1
        assert gs["tests"][0]["name"] == "1.1"  # qid as-is for Gradescope outline
        assert gs["tests"][0]["score"] == 2

    def test_parse_report_flags_unexpected_question_ids(self, tmp_path):
        """Unexpected student question IDs should produce warning and be reported."""
        submissions_dir = tmp_path / "submissions"
        parsed_dir = tmp_path / "parsed"
        output_dir = tmp_path / "output"
        submissions_dir.mkdir()
        parsed_dir.mkdir()
        output_dir.mkdir()

        sol_path = tmp_path / "solution.ipynb"
        sol_path.write_text(json.dumps(_minimal_notebook_with_question()))

        # Student uses a non-canonical QID 3.1 instead of 1.1.
        student_nb = _make_notebook(
            [
                _md_cell("# <font color='red'>3 Section</font>"),
                _md_cell("- Q3.1 <font color='blue'>[2 PTS] Write code</font>"),
                _code_cell("x = 42"),
            ]
        )
        (submissions_dir / "Alice.ipynb").write_text(json.dumps(student_nb))

        config = {
            "assignment_name": "SmokeTest",
            "model": DEFAULT_MODEL,
            "solution_notebook": str(sol_path),
            "output_dir": str(output_dir),
            "submissions_dir": str(submissions_dir),
            "parsed_dir": str(parsed_dir),
            "parsing": {
                "section_regex": r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
                "question_regex": r"(?i)^\s*-\s*Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
                "keep_images": True,
            },
            "grading": {"question_groups": [["1.1"]], "grade_only": None},
            "rubrics": {},
        }

        _, report = parse_all_students(ensure_app_config(config))
        assert len(report) == 1
        row = report[0]
        assert row["status"] == "warning"
        assert row["questions_missing"] == ["1.1"]
        assert row["questions_unexpected"] == ["3.1"]
        assert row["questions_expected_count"] == 1
        assert row["questions_matched_count"] == 0

    def test_parse_report_flags_duplicate_question_ids(self, tmp_path):
        """Duplicate question labels in a student notebook should be surfaced."""
        submissions_dir = tmp_path / "submissions"
        parsed_dir = tmp_path / "parsed"
        output_dir = tmp_path / "output"
        submissions_dir.mkdir()
        parsed_dir.mkdir()
        output_dir.mkdir()

        sol_path = tmp_path / "solution.ipynb"
        sol_path.write_text(json.dumps(_minimal_notebook_with_question()))

        student_nb = _make_notebook(
            [
                _md_cell("# <font color='red'>1 Section</font>"),
                _md_cell("- Q1.1 <font color='blue'>[2 PTS] First copy</font>"),
                _code_cell("x = 1"),
                _md_cell("- Q1.1 <font color='blue'>[2 PTS] Duplicate copy</font>"),
                _code_cell("x = 2"),
            ]
        )
        (submissions_dir / "Alice.ipynb").write_text(json.dumps(student_nb))

        config = {
            "assignment_name": "SmokeTest",
            "model": DEFAULT_MODEL,
            "solution_notebook": str(sol_path),
            "output_dir": str(output_dir),
            "submissions_dir": str(submissions_dir),
            "parsed_dir": str(parsed_dir),
            "parsing": {
                "section_regex": r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
                "question_regex": r"(?i)^\s*-\s*Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
                "keep_images": True,
            },
            "grading": {"question_groups": [["1.1"]], "grade_only": None},
            "rubrics": {},
        }

        _, report = parse_all_students(ensure_app_config(config))
        assert len(report) == 1
        row = report[0]
        assert row["status"] == "warning"
        assert row["questions_missing"] == []
        assert row["questions_unexpected"] == []
        assert row["questions_duplicate"] == ["1.1"]
        assert row["questions_expected_count"] == 1
        assert row["questions_matched_count"] == 1
