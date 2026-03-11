"""Tests for app.py: path traversal, upload limit, input validation."""

import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import app, MAX_UPLOAD_BYTES


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
        oversized = b"x" * (MAX_UPLOAD_BYTES + 1)
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
