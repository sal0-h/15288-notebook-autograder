"""Tests for utils.py: config I/O, logging, path helpers."""

import logging
from pathlib import Path

import pytest

from config_models import (
    AppConfig,
    AssignmentOutputPaths,
    ensure_app_config,
    get_assignment_output_paths,
    load_app_config,
    save_config,
)
from utils import sanitize_filename_component, setup_assignment_logging


def _load_config_dict(config_path):
    return load_app_config(config_path).model_dump(mode="python")


class TestLoadConfig:
    def test_path_resolution_relative_to_config(self, tmp_path):
        config_path = tmp_path / "output" / "Test" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            """
assignment_name: Test
output_dir: output
solution_notebook: archive/sol.ipynb
parsing:
  section_regex: '[0-9]+'
  question_regex: 'Q[0-9]+'
grading:
  question_groups: [["1.1"]]
""",
            encoding="utf-8",
        )
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "sol.ipynb").write_text("{}", encoding="utf-8")
        cfg = _load_config_dict(config_path)
        assert "sol.ipynb" in cfg["solution_notebook"]

    def test_missing_config_raises_by_default(self, tmp_path):
        missing = tmp_path / "output" / "Missing" / "config.yaml"
        with pytest.raises(FileNotFoundError):
            load_app_config(missing)


class TestSaveConfig:
    def test_round_trip(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
assignment_name: Test
model: gpt-4.1-mini
output_dir: output
solution_notebook: sol.ipynb
parsing:
  section_regex: "\\\\d+"
  question_regex: "Q\\\\d+"
grading:
  question_groups: [["1.1"]]
""",
            encoding="utf-8",
        )
        (tmp_path / "sol.ipynb").write_text("{}", encoding="utf-8")
        cfg = _load_config_dict(config_path)
        save_config(cfg, config_path)
        cfg2 = _load_config_dict(config_path)
        assert cfg2.get("assignment_name") == cfg.get("assignment_name")

    def test_save_load_preserves_grade_flags_and_review_controls(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        (tmp_path / "archive").mkdir(parents=True)
        (tmp_path / "archive" / "sol.ipynb").write_text("{}", encoding="utf-8")

        cfg = {
            "assignment_name": "LabTest_3_S26",
            "model": "gpt-4.1-mini",
            "rubric_model": "",
            "rubric_review": True,
            "include_reference_in_grading": True,
            "solution_notebook": str(tmp_path / "archive" / "sol.ipynb"),
            "output_dir": "output",
            "workers": 1,
            "rubrics": {},
            "max_prompt_tokens": 80_000,
            "max_completion_tokens": 4_096,
            "parsing": {
                "section_regex": r"\\d+",
                "question_regex": r"Q\\d+",
                "keep_images": True,
            },
            "grading": {
                "question_groups": [["1.1"]],
                "grade_only": ["1.1"],
                "grade_only_merge": True,
            },
        }

        save_config(cfg, config_path)
        loaded = _load_config_dict(config_path)

        assert loaded["rubric_review"] is True
        assert loaded["grading"]["grade_only_merge"] is True


class TestSetupAssignmentLogging:
    def test_switches_autograder_log_file(self, tmp_path):
        first = setup_assignment_logging("one", tmp_path / "one")
        second = setup_assignment_logging("two", tmp_path / "two")

        logger_one = logging.getLogger("autograder.one")
        file_handlers_one = [
            h for h in logger_one.handlers if isinstance(h, logging.FileHandler)
        ]

        assert first.name == "autograder.log"
        assert second.name == "autograder.log"
        assert len(file_handlers_one) == 1
        assert Path(file_handlers_one[0].baseFilename).resolve() == first.resolve()


class TestOpenAIClientConfig:
    def test_get_openai_client_uses_httpx_limits_and_timeout(self, monkeypatch):
        from llm import client as llm_client

        monkeypatch.setenv("key", "")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        captured = {}

        def fake_httpx_client(*, limits, timeout):
            captured["limits"] = limits
            captured["timeout"] = timeout
            return "http-client"

        def fake_openai(**kwargs):
            captured["openai_kwargs"] = kwargs
            return "openai-client"

        monkeypatch.setattr(llm_client.httpx, "Client", fake_httpx_client)
        monkeypatch.setattr(llm_client, "OpenAI", fake_openai)

        client = llm_client.get_openai_client(
            max_retries=7,
            max_connections=11,
            max_keepalive_connections=9,
            read_timeout_s=123.0,
            pool_timeout_s=17.0,
        )

        assert client == "openai-client"
        assert captured["limits"].max_connections == 11
        assert captured["openai_kwargs"]["api_key"] == "test-key"


class TestSanitizeFilenameComponent:
    def test_strips_forbidden_chars(self):
        assert sanitize_filename_component('a/b:c*d?e"f<g>h|i') == "abcdefghi"

    def test_if_empty(self):
        assert sanitize_filename_component("///", if_empty="x") == "x"


class TestAssignmentOutputPaths:
    def test_returns_typed_paths(self, tmp_path):
        cfg = AppConfig(
            assignment_name="HW1",
            output_dir=str(tmp_path),
            parsed_dir=str(tmp_path / "parsed"),
        )

        paths = get_assignment_output_paths(cfg)

        assert isinstance(paths, AssignmentOutputPaths)
        assert paths.graded_results == tmp_path / "graded_results.json"

    def test_accepts_dict_config(self, tmp_path):
        raw = {
            "assignment_name": "HW1",
            "model": "gpt-4.1-mini",
            "solution_notebook": "",
            "output_dir": str(tmp_path),
            "parsed_dir": str(tmp_path / "parsed"),
            "submissions_dir": str(tmp_path / "submissions"),
            "parsing": {
                "section_regex": r"\\d+",
                "question_regex": r"Q\\d+",
                "keep_images": True,
            },
            "grading": {"question_groups": [["1.1"]], "grade_only": None},
            "rubrics": {},
        }

        paths = get_assignment_output_paths(ensure_app_config(raw))
        assert paths.output_dir == tmp_path
