"""Tests for app.py: path traversal, upload limit, input validation."""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import app, DEFAULT_UPLOAD_MB


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_config(tmp_path):
    """Config that uses tmp_path for output."""
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "parsed_dir": str(parsed_dir),
        "output_dir": str(output_dir),
        "submissions_dir": str(tmp_path / "submissions"),
    }


def _full_config(tmp_path):
    """Full config valid for AppConfig (for endpoints that call save_config)."""
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    sol = tmp_path / "sol.ipynb"
    sol.write_text("{}")
    return {
        "assignment_name": "Test",
        "model": "gpt-4o-mini",
        "solution_notebook": str(sol),
        "parsed_dir": str(parsed_dir),
        "output_dir": str(output_dir),
        "submissions_dir": str(tmp_path / "submissions"),
        "parsing": {"section_regex": "\\d+", "question_regex": "Q\\d+", "keep_images": True},
        "grading": {"question_groups": [["1.1"]], "grade_only": None},
        "prompts": {"system": "Grade."},
        "rubrics": {},
    }


class TestPathTraversal:
    def test_parsed_path_traversal_rejected(self, client, mock_config, tmp_path):
        with patch("app.load_config", return_value=mock_config):
            (tmp_path / "parsed").mkdir(exist_ok=True)
            r = client.get("/parsed/..%2F..%2F..%2Fetc%2Fpasswd")
            assert r.status_code in (400, 404)
            if r.status_code == 400:
                assert "Path traversal" in r.json().get("detail", "")

    def test_ui_path_traversal_rejected(self, client):
        r = client.get("/ui/../.env")
        assert r.status_code in (400, 404)


class TestUploadLimit:
    def test_oversized_upload_rejected(self, client):
        oversized = b"x" * (DEFAULT_UPLOAD_MB * 1024 * 1024 + 1)
        r = client.post(
            "/gather",
            files={"zip_file": ("large.zip", io.BytesIO(oversized), "application/zip")},
        )
        assert r.status_code == 413

    def test_invalid_zip_rejected(self, client):
        r = client.post(
            "/gather",
            files={"zip_file": ("fake.zip", io.BytesIO(b"not a zip"), "application/zip")},
        )
        assert r.status_code == 400
        assert "Invalid" in r.json().get("detail", "")


class TestPutResults:
    def test_malformed_payload_rejected(self, client, mock_config, tmp_path):
        (tmp_path / "output").mkdir(exist_ok=True)
        graded = tmp_path / "output" / "graded_results.json"
        graded.write_text("[]", encoding="utf-8")
        with patch("app.load_config", return_value=mock_config):
            r = client.put(
                "/results/Alice",
                json={"student_name": "Alice"},  # missing questions, total_score, total_max
            )
            assert r.status_code == 422

    def test_valid_payload_accepted(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        graded = out / "graded_results.json"
        graded.write_text("[]", encoding="utf-8")
        with patch("app.load_config", return_value=mock_config):
            r = client.put(
                "/results/Alice",
                json={
                    "student_name": "Alice",
                    "questions": {"1.1": {"score": 1, "max": 1, "feedback": ""}},
                    "total_score": 1,
                    "total_max": 1,
                },
            )
            assert r.status_code == 200

    def test_path_traversal_in_student_name_rejected(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        graded = out / "graded_results.json"
        graded.write_text("[]", encoding="utf-8")
        with patch("app.load_config", return_value=mock_config):
            r = client.put(
                "/results/..%2F..%2Fetc",
                json={
                    "student_name": "../../etc",
                    "questions": {},
                    "total_score": 0,
                    "total_max": 1,
                },
            )
            assert r.status_code == 400


class TestGradingLock:
    def test_grade_returns_409_when_lock_held(self, client):
        with patch("app._grading_lock") as mock_lock:
            mock_lock.acquire.return_value = False
            r = client.get("/grade")
            assert r.status_code == 409


class TestRubricsEndpoints:
    def test_get_rubrics_returns_config_rubrics(self, client, mock_config):
        mock_config["rubrics"] = {"1.1": {"points": 2, "criteria": "Check plot."}}
        with patch("app.load_config", return_value=mock_config):
            r = client.get("/rubrics")
        assert r.status_code == 200
        assert r.json() == {"1.1": {"points": 2, "criteria": "Check plot."}}

    def test_put_rubrics_saves_to_config(self, client, tmp_path):
        cfg = _full_config(tmp_path)
        cfg["rubrics"] = {}
        with patch("app.load_config", return_value=cfg), patch("app.save_config") as mock_save:
            r = client.put("/rubrics", json={"1.1": {"points": 2, "criteria": "Full marks: correct."}})
        assert r.status_code == 200
        mock_save.assert_called_once()

    def test_generate_rubrics_returns_rubrics(self, client, mock_config, tmp_path):
        (tmp_path / "output").mkdir(exist_ok=True)
        (tmp_path / "output" / "solution_parsed.json").write_text(
            json.dumps({"sections": {"1": {"questions": {"1.1": {"points": 2, "question_markdown": "Q1.1"}}}}})
        )
        mock_config["output_dir"] = str(tmp_path / "output")
        mock_config["grading"] = {"question_groups": [["1.1"]]}
        with patch("app.load_config", return_value=mock_config), patch(
            "app.generate_rubrics", return_value={"1.1": {"points": 2, "criteria": "ok"}}
        ), patch("app.save_config"):
            r = client.post("/generate-rubrics")
        assert r.status_code == 200
        assert "1.1" in r.json().get("rubrics", {})


class TestCalibrateEndpoint:
    def test_calibrate_returns_flagged(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        graded = [
            {"student_name": "A", "questions": {"1.1": {"score": 0, "max": 10}}, "total_score": 0, "total_max": 10},
            {"student_name": "B", "questions": {"1.1": {"score": 0, "max": 10}}, "total_score": 0, "total_max": 10},
            {"student_name": "C", "questions": {"1.1": {"score": 0, "max": 10}}, "total_score": 0, "total_max": 10},
            {"student_name": "D", "questions": {"1.1": {"score": 0, "max": 10}}, "total_score": 0, "total_max": 10},
            {"student_name": "E", "questions": {"1.1": {"score": 0, "max": 10}}, "total_score": 0, "total_max": 10},
            {"student_name": "Outlier", "questions": {"1.1": {"score": 10, "max": 10}}, "total_score": 10, "total_max": 10},
        ]
        (out / "graded_results.json").write_text(json.dumps(graded))
        mock_config["output_dir"] = str(out)
        with patch("app.load_config", return_value=mock_config):
            r = client.post("/calibrate")
        assert r.status_code == 200
        data = r.json()
        assert "flagged" in data
        assert data["count"] >= 1


class TestResultsGet:
    def test_get_results_returns_graded_list(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graded_results.json").write_text('[{"student_name": "Alice", "questions": {}, "total_score": 5, "total_max": 10}]')
        mock_config["output_dir"] = str(out)
        with patch("app.load_config", return_value=mock_config):
            r = client.get("/results")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["student_name"] == "Alice"

    def test_get_results_empty_when_no_file(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        mock_config["output_dir"] = str(out)
        with patch("app.load_config", return_value=mock_config):
            r = client.get("/results")
        assert r.status_code == 200
        assert r.json() == []


class TestParsedEndpoint:
    def test_get_parsed_success(self, client, mock_config, tmp_path):
        parsed_dir = tmp_path / "parsed"
        parsed_dir.mkdir(parents=True, exist_ok=True)
        (parsed_dir / "Alice.json").write_text('{"sections": {}, "student_name": "Alice"}')
        mock_config["parsed_dir"] = str(parsed_dir)
        with patch("app.load_config", return_value=mock_config):
            r = client.get("/parsed/Alice")
        assert r.status_code == 200
        assert r.json().get("student_name") == "Alice"


class TestExportEndpoint:
    def test_export_returns_summary(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graded_results.json").write_text('[{"student_name": "Alice", "questions": {"1.1": {"score": 1, "max": 1}}, "total_score": 1, "total_max": 1}]')
        mock_config["output_dir"] = str(out)
        with patch("app.load_config", return_value=mock_config), patch("app.export_all") as mock_export:
            mock_export.return_value = {"students": 1, "excel_path": str(out / "Final_Grades.xlsx")}
            r = client.post("/export")
        assert r.status_code == 200
        assert r.json().get("students") == 1
