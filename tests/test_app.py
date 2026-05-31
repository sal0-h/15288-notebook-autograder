"""Tests for app.py: path traversal, upload limit, API contracts."""

import io
import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile

from api import state
from api.routers.pipeline_routes import DEFAULT_UPLOAD_MB
from app import app
from config_models import (
    DEFAULT_MODEL,
    RubricEntry,
    RubricItem,
    default_config,
    ensure_app_config,
)
from results_models import GradedResult


def _active_assignment_pair(config_like: dict) -> tuple[dict, object]:
    merged = {**default_config("Test"), **config_like}
    cfg = ensure_app_config(merged)
    return cfg.model_dump(mode="python"), cfg


@contextmanager
def patch_active_assignment(config_like: dict):
    _, app_cfg = _active_assignment_pair(config_like)
    with patch("api.state.get_active_app_config", return_value=app_cfg):
        yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def mock_config(tmp_path):
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
    parsed_dir = tmp_path / "parsed"
    parsed_dir.mkdir(parents=True, exist_ok=True)
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    sol = tmp_path / "sol.ipynb"
    sol.write_text("{}")
    return {
        "assignment_name": "Test",
        "model": DEFAULT_MODEL,
        "solution_notebook": str(sol),
        "parsed_dir": str(parsed_dir),
        "output_dir": str(output_dir),
        "submissions_dir": str(tmp_path / "submissions"),
        "parsing": {
            "section_regex": "\\d+",
            "question_regex": "Q\\d+",
            "keep_images": True,
        },
        "grading": {"question_groups": [["1.1"]], "grade_only": None},
        "rubrics": {},
    }


class TestParseSolution:
    def test_parse_solution_empty_notebook_path_returns_404(self, client, tmp_path):
        with (
            patch.object(state, "PROJECT_ROOT", tmp_path),
            patch(
                "api.state.get_active_app_config",
                return_value=ensure_app_config(
                    {
                        **default_config("EmptySol"),
                        "assignment_name": "EmptySol",
                        "solution_notebook": "",
                    }
                ),
            ),
        ):
            r = client.post("/parse-solution")
        assert r.status_code == 404
        assert "not set" in r.json()["detail"].lower()


class TestPathTraversal:
    def test_parsed_path_traversal_rejected(self, client, mock_config, tmp_path):
        with patch_active_assignment(mock_config):
            (tmp_path / "parsed").mkdir(exist_ok=True)
            r = client.get("/parsed/..%2F..%2F..%2Fetc%2Fpasswd")
            assert r.status_code in (400, 404)

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
            files={
                "zip_file": ("fake.zip", io.BytesIO(b"not a zip"), "application/zip")
            },
        )
        assert r.status_code == 400


class TestConfigEndpoints:
    def test_load_or_create_creates_new_config(self, client, tmp_path):
        with (
            patch.object(state, "PROJECT_ROOT", tmp_path),
            patch("api.routers.config_routes.save_config") as mock_save,
            patch(
                "api.routers.config_routes.load_app_config",
                return_value=ensure_app_config(
                    {"assignment_name": "NewLab", "model": DEFAULT_MODEL}
                ),
            ),
        ):
            r = client.post("/load-or-create", json={"assignment_name": "NewLab"})
        assert r.status_code == 200
        assert r.json()["created"] is True
        mock_save.assert_called_once()

    def test_load_or_create_loads_existing_config(self, client, tmp_path):
        config_path = tmp_path / "output" / "ExistingLab" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("assignment_name: ExistingLab\n", encoding="utf-8")
        with (
            patch.object(state, "PROJECT_ROOT", tmp_path),
            patch(
                "api.routers.config_routes.load_app_config",
                return_value=ensure_app_config(
                    {"assignment_name": "ExistingLab", "model": DEFAULT_MODEL}
                ),
            ),
        ):
            r = client.post("/load-or-create", json={"assignment_name": "ExistingLab"})
        assert r.status_code == 200
        assert r.json()["created"] is False

    def test_put_config_accepts_partial_payload(self, client, tmp_path):
        existing = {
            "assignment_name": "default",
            "model": DEFAULT_MODEL,
            "solution_notebook": str(tmp_path / "sol.ipynb"),
            "output_dir": str(tmp_path / "output"),
            "submissions_dir": str(tmp_path / "output" / "submissions"),
            "parsed_dir": str(tmp_path / "output" / "parsed"),
            "parsing": {
                "section_regex": r"\d+",
                "question_regex": r"Q\d+",
                "keep_images": True,
            },
            "grading": {"question_groups": [["1.1"]]},
            "rubrics": {},
        }

        with (
            patch(
                "api.state.get_active_app_config",
                return_value=ensure_app_config(existing),
            ),
            patch.object(state, "_active_config_path", Path("output/Test/config.yaml")),
            patch("api.routers.config_routes.save_config") as mock_save,
        ):
            r = client.put(
                "/config",
                json={"assignment_name": "LabTest_Demo", "model": DEFAULT_MODEL},
            )

        assert r.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved["assignment_name"] == "LabTest_Demo"
        assert "parsing" in saved

    def test_put_config_assignment_change_clears_solution_when_missing(
        self, client, tmp_path
    ):
        existing = {
            "assignment_name": "Old_Assignment",
            "model": DEFAULT_MODEL,
            "solution_notebook": str(tmp_path / "old_sol.ipynb"),
            "output_dir": str(tmp_path / "output"),
            "submissions_dir": str(tmp_path / "output" / "submissions"),
            "parsed_dir": str(tmp_path / "output" / "parsed"),
            "parsing": {
                "section_regex": r"\d+",
                "question_regex": r"Q\d+",
                "keep_images": True,
            },
            "grading": {"question_groups": [["1.1"]]},
            "rubrics": {},
        }

        with (
            patch(
                "api.state.get_active_app_config",
                return_value=ensure_app_config(existing),
            ),
            patch.object(state, "_active_config_path", Path("output/Test/config.yaml")),
            patch("api.routers.config_routes.save_config") as mock_save,
        ):
            r = client.put(
                "/config",
                json={"assignment_name": "New_Assignment", "model": DEFAULT_MODEL},
            )

        assert r.status_code == 200
        assert mock_save.call_args[0][0]["solution_notebook"] == ""


class TestPutResults:
    def test_valid_payload_accepted(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graded_results.json").write_text("[]", encoding="utf-8")
        with patch_active_assignment(mock_config):
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

    def test_path_traversal_in_student_name_rejected(
        self, client, mock_config, tmp_path
    ):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graded_results.json").write_text("[]", encoding="utf-8")
        with patch_active_assignment(mock_config):
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
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False
        with patch.object(state, "grading_lock", mock_lock):
            r = client.get("/grade")
            assert r.status_code == 409


class TestGradeOneMerge:
    def test_grade_one_uses_merge_into_when_grade_only_merge_enabled(
        self, client, tmp_path
    ):
        parsed_dir = tmp_path / "parsed"
        parsed_dir.mkdir(parents=True, exist_ok=True)
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        solution = {
            "sections": {
                "1": {"questions": {"1.1": {"points": 2}, "1.2": {"points": 2}}}
            }
        }
        student = {
            "sections": {
                "1": {"questions": {"1.1": {"points": 2}, "1.2": {"points": 2}}}
            }
        }
        existing = {
            "student_name": "Alice",
            "questions": {
                "1.1": {"score": 2, "max": 2, "feedback": "ok"},
                "1.2": {"score": 0, "max": 2, "feedback": "old"},
            },
            "total_score": 2,
            "total_max": 4,
            "summary_feedback": "Q1.2: old",
        }

        (output_dir / "solution_parsed.json").write_text(
            json.dumps(solution), encoding="utf-8"
        )
        (parsed_dir / "Alice.json").write_text(json.dumps(student), encoding="utf-8")
        (output_dir / "graded_results.json").write_text(
            json.dumps([existing]), encoding="utf-8"
        )

        cfg = _full_config(tmp_path)
        cfg["parsed_dir"] = str(parsed_dir)
        cfg["output_dir"] = str(output_dir)
        cfg["grading"] = {
            "question_groups": [["1.1", "1.2"]],
            "grade_only": ["1.2"],
            "grade_only_merge": True,
        }

        returned = GradedResult(
            student_name="Alice",
            questions={
                "1.1": {"score": 2, "max": 2, "feedback": "ok"},
                "1.2": {"score": 2, "max": 2, "feedback": "new"},
            },
            total_score=4,
            total_max=4,
            summary_feedback="Full marks.",
        )

        with (
            patch_active_assignment(cfg),
            patch(
                "api.routers.grade_routes.grade_student",
                return_value=returned,
            ) as mock_grade_student,
        ):
            r = client.post("/grade/Alice")

        assert r.status_code == 200
        assert mock_grade_student.call_args[0][5].questions["1.2"].feedback == "old"


class TestRubricsEndpoints:
    def test_get_rubrics_returns_config_rubrics(self, client, mock_config):
        mock_config["rubrics"] = {
            "1.1": {
                "points": 2,
                "items": [{"description": "Check plot.", "deduction": 2.0}],
            }
        }
        with patch(
            "api.state.get_active_app_config",
            return_value=ensure_app_config(mock_config),
        ):
            r = client.get("/rubrics")
        assert r.status_code == 200
        assert "1.1" in r.json()


class TestExportEndpoint:
    def test_export_returns_summary(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graded_results.json").write_text(
            '[{"student_name": "Alice", "questions": {"1.1": {"score": 1, "max": 1}}, '
            '"total_score": 1, "total_max": 1}]'
        )
        mock_config["output_dir"] = str(out)
        with (
            patch_active_assignment(mock_config),
            patch("api.routers.export_routes.export_all") as mock_export,
        ):
            mock_export.return_value = {
                "students": 1,
                "excel_path": str(out / "Final_Grades.xlsx"),
            }
            r = client.post("/export")
        assert r.status_code == 200
        assert r.json().get("students") == 1


class TestDetectGenai:
    def test_post_detect_genai_returns_summary(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graded_results.json").write_text("[]")
        mock_config["output_dir"] = str(out)
        with (
            patch_active_assignment(mock_config),
            patch(
                "api.routers.grade_routes.run_genai_detection",
                return_value={
                    "students_processed": 0,
                    "questions_flagged": 0,
                    "students_skipped": 0,
                    "errors": [],
                    "graded_results_path": str(out / "graded_results.json"),
                },
            ),
        ):
            r = client.post("/detect-genai")
        assert r.status_code == 200
        assert "graded_results_path" in r.json()
