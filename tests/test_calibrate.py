"""Tests for calibrate.py: z-score outlier detection."""

import json
from pathlib import Path

import pytest

from calibrate import run_calibration


class TestRunCalibration:
    def test_empty_results_returns_empty(self, tmp_path):
        (tmp_path / "graded_results.json").write_text("[]", encoding="utf-8")
        config = {"output_dir": str(tmp_path)}
        assert run_calibration(config) == []

    def test_single_student_no_outliers_flagged(self, tmp_path):
        """With n < 2 per question, no std; nothing flagged."""
        data = [
            {
                "student_name": "Alice",
                "questions": {
                    "1.1": {"score": 2, "max": 3},
                    "1.2": {"score": 1, "max": 2},
                },
                "total_score": 3,
                "total_max": 5,
            }
        ]
        (tmp_path / "graded_results.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        config = {"output_dir": str(tmp_path)}
        assert run_calibration(config) == []

    def test_flags_low_outlier(self, tmp_path):
        """Student with score 2+ std below mean is flagged."""
        data = [
            {
                "student_name": "A",
                "questions": {"1.1": {"score": 10, "max": 10}},
                "total_score": 10,
                "total_max": 10,
            },
            {
                "student_name": "B",
                "questions": {"1.1": {"score": 10, "max": 10}},
                "total_score": 10,
                "total_max": 10,
            },
            {
                "student_name": "C",
                "questions": {"1.1": {"score": 10, "max": 10}},
                "total_score": 10,
                "total_max": 10,
            },
            {
                "student_name": "D",
                "questions": {"1.1": {"score": 10, "max": 10}},
                "total_score": 10,
                "total_max": 10,
            },
            {
                "student_name": "E",
                "questions": {"1.1": {"score": 10, "max": 10}},
                "total_score": 10,
                "total_max": 10,
            },
            {
                "student_name": "Dave",
                "questions": {"1.1": {"score": 0, "max": 10}},
                "total_score": 0,
                "total_max": 10,
            },
        ]
        (tmp_path / "graded_results.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        config = {"output_dir": str(tmp_path)}
        flagged = run_calibration(config)
        assert len(flagged) == 1
        assert flagged[0]["student_name"] == "Dave"
        assert flagged[0]["qid"] == "1.1"
        assert flagged[0]["score"] == 0
        assert flagged[0]["flag_reason"] == "low"
        assert flagged[0]["z_score"] < -2

    def test_flags_high_outlier(self, tmp_path):
        """Student with score 2+ std above mean is flagged."""
        data = [
            {
                "student_name": "A",
                "questions": {"2.1": {"score": 0, "max": 5}},
                "total_score": 0,
                "total_max": 5,
            },
            {
                "student_name": "B",
                "questions": {"2.1": {"score": 0, "max": 5}},
                "total_score": 0,
                "total_max": 5,
            },
            {
                "student_name": "C",
                "questions": {"2.1": {"score": 0, "max": 5}},
                "total_score": 0,
                "total_max": 5,
            },
            {
                "student_name": "D",
                "questions": {"2.1": {"score": 0, "max": 5}},
                "total_score": 0,
                "total_max": 5,
            },
            {
                "student_name": "E",
                "questions": {"2.1": {"score": 0, "max": 5}},
                "total_score": 0,
                "total_max": 5,
            },
            {
                "student_name": "Outlier",
                "questions": {"2.1": {"score": 5, "max": 5}},
                "total_score": 5,
                "total_max": 5,
            },
        ]
        (tmp_path / "graded_results.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        config = {"output_dir": str(tmp_path)}
        flagged = run_calibration(config)
        assert len(flagged) == 1
        assert flagged[0]["student_name"] == "Outlier"
        assert flagged[0]["flag_reason"] == "high"
        assert flagged[0]["z_score"] > 2

    def test_writes_calibration_report(self, tmp_path):
        """calibration_report.json is written to output_dir."""
        data = [
            {
                "student_name": "A",
                "questions": {"1.1": {"score": 0, "max": 10}},
                "total_score": 0,
                "total_max": 10,
            },
            {
                "student_name": "B",
                "questions": {"1.1": {"score": 0, "max": 10}},
                "total_score": 0,
                "total_max": 10,
            },
            {
                "student_name": "C",
                "questions": {"1.1": {"score": 0, "max": 10}},
                "total_score": 0,
                "total_max": 10,
            },
            {
                "student_name": "D",
                "questions": {"1.1": {"score": 0, "max": 10}},
                "total_score": 0,
                "total_max": 10,
            },
            {
                "student_name": "E",
                "questions": {"1.1": {"score": 0, "max": 10}},
                "total_score": 0,
                "total_max": 10,
            },
            {
                "student_name": "Outlier",
                "questions": {"1.1": {"score": 10, "max": 10}},
                "total_score": 10,
                "total_max": 10,
            },
        ]
        (tmp_path / "graded_results.json").write_text(
            json.dumps(data, indent=2), encoding="utf-8"
        )
        config = {"output_dir": str(tmp_path)}
        run_calibration(config)
        report_path = tmp_path / "calibration_report.json"
        assert report_path.exists()
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert len(report) == 1
        assert report[0]["student_name"] == "Outlier"

    def test_missing_file_raises(self, tmp_path):
        """FileNotFoundError when graded_results.json does not exist."""
        config = {"output_dir": str(tmp_path)}
        with pytest.raises(FileNotFoundError, match="Graded results not found"):
            run_calibration(config)
