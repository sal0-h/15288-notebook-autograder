"""Tests for app.py: path traversal, upload limit, input validation."""

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile as StarletteUploadFile

from api import state

from api.constants import DEFAULT_UPLOAD_MB
from app import app
from utils import DEFAULT_MODEL


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


class TestPathTraversal:
    def test_parsed_path_traversal_rejected(self, client, mock_config, tmp_path):
        with patch("api.state.get_active_config", return_value=mock_config):
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
            files={
                "zip_file": ("fake.zip", io.BytesIO(b"not a zip"), "application/zip")
            },
        )
        assert r.status_code == 400
        assert "Invalid" in r.json().get("detail", "")

    def test_upload_is_read_in_chunks(self, client):
        read_sizes = []
        original_read = StarletteUploadFile.read

        async def tracking_read(self, size=-1):
            read_sizes.append(size)
            return await original_read(self, size)

        oversized = b"x" * (1024 * 1024 + 1)
        with (
            patch("api.routers.pipeline_routes.DEFAULT_UPLOAD_MB", 1),
            patch("starlette.datastructures.UploadFile.read", new=tracking_read),
        ):
            r = client.post(
                "/gather",
                files={
                    "zip_file": ("large.zip", io.BytesIO(oversized), "application/zip")
                },
            )

        assert r.status_code == 413
        assert read_sizes
        assert read_sizes[0] == 1024 * 1024


class TestConfigEndpoints:
    def test_load_or_create_creates_new_config(self, client, tmp_path):
        """Creates a new config file when the assignment folder does not exist."""
        with (
            patch.object(state, "PROJECT_ROOT", tmp_path),
            patch("api.routers.config_routes.save_config") as mock_save,
            patch(
                "api.routers.config_routes.load_config",
                return_value={"assignment_name": "NewLab", "model": DEFAULT_MODEL},
            ),
        ):
            r = client.post("/load-or-create", json={"assignment_name": "NewLab"})
        assert r.status_code == 200
        data = r.json()
        assert data["assignment_name"] == "NewLab"
        assert data["created"] is True
        assert "config" in data
        mock_save.assert_called_once()

    def test_load_or_create_loads_existing_config(self, client, tmp_path):
        """Loads existing config when the assignment folder already exists."""
        config_path = tmp_path / "output" / "ExistingLab" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("assignment_name: ExistingLab\n", encoding="utf-8")
        with (
            patch.object(state, "PROJECT_ROOT", tmp_path),
            patch(
                "api.routers.config_routes.load_config",
                return_value={"assignment_name": "ExistingLab", "model": DEFAULT_MODEL},
            ) as mock_load,
        ):
            r = client.post("/load-or-create", json={"assignment_name": "ExistingLab"})
        assert r.status_code == 200
        data = r.json()
        assert data["assignment_name"] == "ExistingLab"
        assert data["created"] is False

    def test_load_or_create_missing_name_rejected(self, client):
        r = client.post("/load-or-create", json={})
        assert r.status_code == 400
        assert "assignment_name" in r.json().get("detail", "")

    def test_put_config_accepts_partial_payload(self, client, tmp_path):
        existing = {
            "assignment_name": "default",
            "model": DEFAULT_MODEL,
            "rubric_model": "",
            "include_reference_in_grading": False,
            "solution_notebook": str(tmp_path / "sol.ipynb"),
            "output_dir": str(tmp_path / "output"),
            "submissions_dir": str(tmp_path / "output" / "submissions"),
            "parsed_dir": str(tmp_path / "output" / "parsed"),
            "workers": 1,
            "max_prompt_tokens": 80_000,
            "max_completion_tokens": 4_096,
            "parsing": {
                "section_regex": r"(?m)^\\s*#\\s*<font[^>]*>\\s*(\\d+)\\b",
                "question_regex": r"(?i)^\\s*(-\\s*)?Q(\\d+)\\.(\\d+)\\s*.*?\\[\\s*(\\d+)\\s*PTS\\s*\\]",
                "keep_images": True,
            },
            "grading": {"question_groups": [["1.1"]]},
            "rubrics": {},
        }

        with (
            patch("api.state.get_active_config", return_value=existing),
            patch.object(
                state,
                "_active_config_path",
                Path("output/Test/config.yaml"),
            ),
            patch("api.routers.config_routes.save_config") as mock_save,
        ):
            r = client.put(
                "/config",
                json={
                    "assignment_name": "LabTest_Demo",
                    "model": DEFAULT_MODEL,
                    "rubric_model": "",
                },
            )

        assert r.status_code == 200
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert saved["assignment_name"] == "LabTest_Demo"
        assert "parsing" in saved
        assert "grading" in saved
        assert "rubric_review" in saved

    def test_put_config_assignment_change_clears_solution_when_missing(
        self, client, tmp_path
    ):
        existing = {
            "assignment_name": "Old_Assignment",
            "model": DEFAULT_MODEL,
            "rubric_model": "",
            "include_reference_in_grading": False,
            "solution_notebook": str(tmp_path / "old_sol.ipynb"),
            "output_dir": str(tmp_path / "output"),
            "submissions_dir": str(tmp_path / "output" / "submissions"),
            "parsed_dir": str(tmp_path / "output" / "parsed"),
            "workers": 1,
            "max_prompt_tokens": 80_000,
            "max_completion_tokens": 4_096,
            "parsing": {
                "section_regex": r"(?m)^\\s*#\\s*<font[^>]*>\\s*(\\d+)\\b",
                "question_regex": r"(?i)^\\s*(-\\s*)?Q(\\d+)\\.(\\d+)\\s*.*?\\[\\s*(\\d+)\\s*PTS\\s*\\]",
                "keep_images": True,
            },
            "grading": {"question_groups": [["1.1"]]},
            "rubrics": {},
        }

        with (
            patch("api.state.get_active_config", return_value=existing),
            patch.object(
                state,
                "_active_config_path",
                Path("output/Test/config.yaml"),
            ),
            patch("api.routers.config_routes.save_config") as mock_save,
        ):
            r = client.put(
                "/config",
                json={
                    "assignment_name": "New_Assignment",
                    "model": DEFAULT_MODEL,
                },
            )

        assert r.status_code == 200
        saved = mock_save.call_args[0][0]
        assert saved["assignment_name"] == "New_Assignment"
        assert saved["solution_notebook"] == ""

    def test_get_config_returns_500_when_active_config_load_fails(self, client):
        with (
            patch.object(
                state,
                "_active_config_path",
                Path("output/Test/config.yaml"),
            ),
            patch(
                "api.routers.config_routes.load_config",
                side_effect=ValueError("bad yaml"),
            ),
        ):
            r = client.get("/config")
        assert r.status_code == 500
        assert "Failed to load active assignment config" in r.json().get("detail", "")

    def test_put_config_returns_500_when_active_config_load_fails(self, client):
        with (
            patch.object(
                state,
                "_active_config_path",
                Path("output/Test/config.yaml"),
            ),
            patch("api.state.get_active_config", side_effect=ValueError("bad yaml")),
            patch("api.routers.config_routes.save_config") as mock_save,
        ):
            r = client.put(
                "/config",
                json={
                    "assignment_name": "LabTest_Demo",
                    "model": DEFAULT_MODEL,
                },
            )
        assert r.status_code == 500
        assert "Failed to load active assignment config" in r.json().get("detail", "")
        mock_save.assert_not_called()


class TestPutResults:
    def test_malformed_payload_rejected(self, client, mock_config, tmp_path):
        (tmp_path / "output").mkdir(exist_ok=True)
        graded = tmp_path / "output" / "graded_results.json"
        graded.write_text("[]", encoding="utf-8")
        with patch("api.state.get_active_config", return_value=mock_config):
            r = client.put(
                "/results/Alice",
                json={
                    "student_name": "Alice"
                },  # missing questions, total_score, total_max
            )
            assert r.status_code == 422

    def test_valid_payload_accepted(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        graded = out / "graded_results.json"
        graded.write_text("[]", encoding="utf-8")
        with patch("api.state.get_active_config", return_value=mock_config):
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
        graded = out / "graded_results.json"
        graded.write_text("[]", encoding="utf-8")
        with patch("api.state.get_active_config", return_value=mock_config):
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

    def test_grade_one_returns_409_when_lock_held(self, client, tmp_path):
        cfg = _full_config(tmp_path)
        mock_lock = MagicMock()
        mock_lock.acquire.return_value = False
        with (
            patch("api.state.get_active_config", return_value=cfg),
            patch.object(state, "grading_lock", mock_lock),
        ):
            r = client.post("/grade/Alice")
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

        returned = {
            "student_name": "Alice",
            "questions": {
                "1.1": {"score": 2, "max": 2, "feedback": "ok"},
                "1.2": {"score": 2, "max": 2, "feedback": "new"},
            },
            "total_score": 4,
            "total_max": 4,
            "summary_feedback": "Full marks.",
        }

        with (
            patch("api.state.get_active_config", return_value=cfg),
            patch(
                "api.routers.grade_routes.grade_student",
                return_value=returned,
            ) as mock_grade_student,
        ):
            r = client.post("/grade/Alice")

        assert r.status_code == 200
        assert mock_grade_student.called
        call_args = mock_grade_student.call_args[0]
        # merge_into is the 6th positional argument in api_grade_one call.
        assert call_args[5]["student_name"] == "Alice"
        assert call_args[5]["questions"]["1.2"]["feedback"] == "old"

    def test_grade_one_does_not_persist_internal_usage(self, client, tmp_path):
        parsed_dir = tmp_path / "parsed"
        parsed_dir.mkdir(parents=True, exist_ok=True)
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        (output_dir / "solution_parsed.json").write_text(
            json.dumps({"sections": {"1": {"questions": {"1.1": {"points": 2}}}}}),
            encoding="utf-8",
        )
        (parsed_dir / "Alice.json").write_text(
            json.dumps(
                {
                    "sections": {"1": {"questions": {"1.1": {"points": 2}}}},
                    "student_name": "Alice",
                }
            ),
            encoding="utf-8",
        )

        cfg = _full_config(tmp_path)
        cfg["parsed_dir"] = str(parsed_dir)
        cfg["output_dir"] = str(output_dir)
        cfg["grading"] = {"question_groups": [["1.1"]], "grade_only": None}

        graded_with_usage = {
            "student_name": "Alice",
            "questions": {"1.1": {"score": 2, "max": 2, "feedback": "ok"}},
            "total_score": 2,
            "total_max": 2,
            "summary_feedback": "Full marks.",
            "_usage": {"prompt_tokens": 11, "completion_tokens": 7},
        }

        with (
            patch("api.state.get_active_config", return_value=cfg),
            patch(
                "api.routers.grade_routes.grade_student",
                return_value=graded_with_usage,
            ),
        ):
            r = client.post("/grade/Alice")

        assert r.status_code == 200
        body = r.json()
        assert "usage" in body
        assert "_usage" not in body["result"]

        saved = json.loads(
            (output_dir / "graded_results.json").read_text(encoding="utf-8")
        )
        assert "_usage" not in saved[0]

    def test_grade_one_merge_uses_results_lock_for_pre_read(self, client, tmp_path):
        parsed_dir = tmp_path / "parsed"
        parsed_dir.mkdir(parents=True, exist_ok=True)
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        (output_dir / "solution_parsed.json").write_text(
            json.dumps({"sections": {"1": {"questions": {"1.1": {"points": 2}}}}}),
            encoding="utf-8",
        )
        (parsed_dir / "Alice.json").write_text(
            json.dumps(
                {
                    "sections": {"1": {"questions": {"1.1": {"points": 2}}}},
                    "student_name": "Alice",
                }
            ),
            encoding="utf-8",
        )
        (output_dir / "graded_results.json").write_text(
            json.dumps(
                [
                    {
                        "student_name": "Alice",
                        "questions": {"1.1": {"score": 1, "max": 2, "feedback": "old"}},
                        "total_score": 1,
                        "total_max": 2,
                        "summary_feedback": "Q1.1: old",
                    }
                ]
            ),
            encoding="utf-8",
        )

        cfg = _full_config(tmp_path)
        cfg["parsed_dir"] = str(parsed_dir)
        cfg["output_dir"] = str(output_dir)
        cfg["grading"] = {
            "question_groups": [["1.1"]],
            "grade_only": ["1.1"],
            "grade_only_merge": True,
        }

        returned = {
            "student_name": "Alice",
            "questions": {"1.1": {"score": 2, "max": 2, "feedback": "new"}},
            "total_score": 2,
            "total_max": 2,
            "summary_feedback": "Full marks.",
        }

        mock_lock = MagicMock()
        mock_lock.__enter__.return_value = None
        mock_lock.__exit__.return_value = None

        with (
            patch("api.state.get_active_config", return_value=cfg),
            patch("api.routers.grade_routes.grade_student", return_value=returned),
            patch.object(state, "results_lock", mock_lock),
        ):
            r = client.post("/grade/Alice")

        assert r.status_code == 200
        # One lock for merge pre-read and one for final write.
        assert mock_lock.__enter__.call_count >= 2


class TestRubricsEndpoints:
    def test_get_rubrics_returns_config_rubrics(self, client, mock_config):
        mock_config["rubrics"] = {
            "1.1": {
                "points": 2,
                "items": [{"description": "Check plot.", "deduction": 2.0}],
            }
        }
        with patch("api.state.get_active_config", return_value=mock_config):
            r = client.get("/rubrics")
        assert r.status_code == 200
        assert r.json() == {
            "1.1": {
                "points": 2,
                "items": [{"description": "Check plot.", "deduction": 2.0}],
            }
        }

    def test_put_rubrics_saves_to_config(self, client, tmp_path):
        cfg = _full_config(tmp_path)
        cfg["rubrics"] = {}
        with (
            patch("api.state.get_active_config", return_value=cfg),
            patch("api.routers.rubric_routes.save_config") as mock_save,
        ):
            r = client.put(
                "/rubrics",
                json={
                    "1.1": {
                        "points": 2,
                        "items": [
                            {"description": "Full marks: correct.", "deduction": 2.0}
                        ],
                    }
                },
            )
        assert r.status_code == 200
        mock_save.assert_called_once()

    def test_generate_rubrics_returns_rubrics(self, client, mock_config, tmp_path):
        (tmp_path / "output").mkdir(exist_ok=True)
        (tmp_path / "output" / "solution_parsed.json").write_text(
            json.dumps(
                {
                    "sections": {
                        "1": {
                            "questions": {
                                "1.1": {"points": 2, "question_markdown": "Q1.1"}
                            }
                        }
                    }
                }
            )
        )
        mock_config["output_dir"] = str(tmp_path / "output")
        mock_config["grading"] = {"question_groups": [["1.1"]]}
        with (
            patch("api.state.get_active_config", return_value=mock_config),
            patch(
                "rubric.generate_rubrics",
                return_value={
                    "1.1": {
                        "points": 2,
                        "items": [{"description": "ok", "deduction": 2.0}],
                    }
                },
            ),
            patch("api.routers.rubric_routes.save_config"),
        ):
            r = client.post("/generate-rubrics")
        assert r.status_code == 200
        assert "1.1" in r.json().get("rubrics", {})


class TestCalibrateEndpoint:
    def test_calibrate_returns_flagged(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        graded = [
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
        (out / "graded_results.json").write_text(json.dumps(graded))
        mock_config["output_dir"] = str(out)
        with patch("api.state.get_active_config", return_value=mock_config):
            r = client.post("/calibrate")
        assert r.status_code == 200
        data = r.json()
        assert "flagged" in data
        assert data["count"] >= 1


class TestResultsGet:
    def test_get_results_returns_graded_list(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graded_results.json").write_text(
            '[{"student_name": "Alice", "questions": {}, "total_score": 5, "total_max": 10}]'
        )
        mock_config["output_dir"] = str(out)
        with patch("api.state.get_active_config", return_value=mock_config):
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
        with patch("api.state.get_active_config", return_value=mock_config):
            r = client.get("/results")
        assert r.status_code == 200
        assert r.json() == []


class TestParsedEndpoint:
    def test_get_parsed_success(self, client, mock_config, tmp_path):
        parsed_dir = tmp_path / "parsed"
        parsed_dir.mkdir(parents=True, exist_ok=True)
        (parsed_dir / "Alice.json").write_text(
            '{"sections": {}, "student_name": "Alice"}'
        )
        mock_config["parsed_dir"] = str(parsed_dir)
        with patch("api.state.get_active_config", return_value=mock_config):
            r = client.get("/parsed/Alice")
        assert r.status_code == 200
        assert r.json().get("student_name") == "Alice"


class TestExportEndpoint:
    def test_export_returns_summary(self, client, mock_config, tmp_path):
        out = tmp_path / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "graded_results.json").write_text(
            '[{"student_name": "Alice", "questions": {"1.1": {"score": 1, "max": 1}}, "total_score": 1, "total_max": 1}]'
        )
        mock_config["output_dir"] = str(out)
        with (
            patch("api.state.get_active_config", return_value=mock_config),
            patch("pipeline_runner.run_export") as mock_export,
        ):
            mock_export.return_value = {
                "students": 1,
                "excel_path": str(out / "Final_Grades.xlsx"),
            }
            r = client.post("/export")
        assert r.status_code == 200
        assert r.json().get("students") == 1
