"""Tests for utils.py: load_app_config, save_config, path resolution."""

import logging

from pathlib import Path

import pytest
import utils

from config_models import AppConfig, ensure_app_config, load_app_config
from utils import (
    AssignmentOutputPaths,
    get_assignment_output_paths,
    sanitize_filename_component,
    save_config,
    setup_assignment_logging,
)


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
        assert "solution_notebook" in cfg
        assert (
            "archive" in cfg["solution_notebook"]
            or "sol.ipynb" in cfg["solution_notebook"]
        )

    def test_assignment_scoping(self, tmp_path):
        config_path = tmp_path / "output" / "LabTest_2" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            """
assignment_name: LabTest_2
output_dir: output
parsing:
  section_regex: '[0-9]+'
  question_regex: 'Q[0-9]+'
grading:
  question_groups: [["1.1"]]
""",
            encoding="utf-8",
        )
        cfg = _load_config_dict(config_path)
        assert "LabTest_2" in cfg["output_dir"]
        assert "submissions" in cfg["submissions_dir"]
        assert "parsed" in cfg["parsed_dir"]

    def test_assignment_name_sanitization(self, tmp_path):
        config_path = tmp_path / "output" / "Test" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            """
assignment_name: "../../etc"
output_dir: output
parsing:
  section_regex: '[0-9]+'
  question_regex: 'Q[0-9]+'
grading:
  question_groups: [["1.1"]]
""",
            encoding="utf-8",
        )
        cfg = _load_config_dict(config_path)
        assert ".." not in cfg["output_dir"]

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
        assert "submissions" in str(cfg2.get("submissions_dir", ""))

    def test_save_config_accepts_appconfig(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        cfg = AppConfig(assignment_name="LabTest_3_S26")

        save_config(cfg, config_path)
        loaded = _load_config_dict(config_path)

        assert loaded["assignment_name"] == "LabTest_3_S26"

    def test_load_app_config_returns_model(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("assignment_name: Demo\n", encoding="utf-8")

        cfg = load_app_config(config_path)

        assert isinstance(cfg, AppConfig)
        assert cfg.assignment_name == "Demo"

    def test_ensure_app_config_accepts_dict_and_model(self):
        cfg_from_dict = ensure_app_config({"assignment_name": "Demo"})
        cfg_from_model = ensure_app_config(cfg_from_dict)

        assert isinstance(cfg_from_dict, AppConfig)
        assert cfg_from_model is cfg_from_dict

    def test_app_config_to_yaml_data_returns_plain_dict(self):
        cfg = AppConfig(assignment_name="Demo")
        data = cfg.model_dump(mode="python", exclude_none=True)

        assert isinstance(data, dict)
        assert data["assignment_name"] == "Demo"

    def test_save_config_preserves_hash_in_assignment_name(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
assignment_name: default
output_dir: output
""",
            encoding="utf-8",
        )

        cfg = {
            "assignment_name": "Lab #1",
            "model": "gpt-4.1-mini",
            "rubric_model": "",
            "rubric_review": True,
            "include_reference_in_grading": False,
            "solution_notebook": "",
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
            "grading": {"question_groups": [["1.1"]], "grade_only": None},
        }

        save_config(cfg, config_path)
        root_cfg = _load_config_dict(config_path)
        assert root_cfg["assignment_name"] == "Lab #1"

    def test_save_config_with_explicit_assignment_config_path(self, tmp_path):
        assignment_config_path = tmp_path / "output" / "LabTest_3_S26" / "config.yaml"
        (tmp_path / "archive").mkdir(parents=True)
        (tmp_path / "archive" / "sol.ipynb").write_text("{}", encoding="utf-8")

        cfg = {
            "assignment_name": "LabTest_3_S26",
            "model": "gpt-4.1-mini",
            "rubric_model": "",
            "rubric_review": True,
            "include_reference_in_grading": False,
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
            "grading": {"question_groups": [["1.1"]], "grade_only": None},
        }

        save_config(cfg, assignment_config_path)
        assignment_cfg_text = assignment_config_path.read_text(encoding="utf-8")
        assert "model: gpt-4.1-mini" in assignment_cfg_text
        assert "solution_notebook:" in assignment_cfg_text
        assert "parsing:" in assignment_cfg_text
        assert "grading:" in assignment_cfg_text
        assert "rubric_review:" in assignment_cfg_text

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
        assert loaded["include_reference_in_grading"] is True
        assert loaded["grading"]["grade_only"] == ["1.1"]
        assert loaded["grading"]["grade_only_merge"] is True

    class TestSetupAssignmentLogging:
        def test_switches_autograder_log_file(self, tmp_path):
            first = setup_assignment_logging("one", tmp_path / "one")
            second = setup_assignment_logging("two", tmp_path / "two")

            logger_one = logging.getLogger("autograder.one")
            logger_two = logging.getLogger("autograder.two")

            file_handlers_one = [
                h for h in logger_one.handlers if isinstance(h, logging.FileHandler)
            ]
            file_handlers_two = [
                h for h in logger_two.handlers if isinstance(h, logging.FileHandler)
            ]

            assert first.name == "autograder.log"
            assert second.name == "autograder.log"
            assert len(file_handlers_one) == 1
            assert len(file_handlers_two) == 1
            assert Path(file_handlers_one[0].baseFilename).resolve() == first.resolve()
            assert Path(file_handlers_two[0].baseFilename).resolve() == second.resolve()

        def test_reconfigures_same_assignment_for_new_output_dir(self, tmp_path):
            assignment_name = "shared-assignment"
            first = setup_assignment_logging(assignment_name, tmp_path / "run_01")
            second = setup_assignment_logging(assignment_name, tmp_path / "run_02")

            logger = logging.getLogger(f"autograder.{assignment_name}")
            file_handlers = [
                h for h in logger.handlers if isinstance(h, logging.FileHandler)
            ]

            assert first.name == "autograder.log"
            assert second.name == "autograder.log"
            assert len(file_handlers) == 1
            assert Path(file_handlers[0].baseFilename).resolve() == second.resolve()


class TestOpenAIClientConfig:
    def test_get_openai_client_uses_httpx_limits_and_timeout(self, monkeypatch):
        import llm_client

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
        assert captured["limits"].max_keepalive_connections == 9
        assert captured["timeout"].read == 123.0
        assert captured["timeout"].pool == 17.0
        assert captured["openai_kwargs"]["api_key"] == "test-key"
        assert captured["openai_kwargs"]["max_retries"] == 7
        assert captured["openai_kwargs"]["http_client"] == "http-client"


class TestSanitizeFilenameComponent:
    def test_strips_forbidden_chars(self):
        assert sanitize_filename_component('a/b:c*d?e"f<g>h|i') == "abcdefghi"

    def test_if_empty(self):
        assert sanitize_filename_component("///", if_empty="x") == "x"
        assert sanitize_filename_component("Alice", if_empty="x") == "Alice"


class TestAssignmentOutputPaths:
    def test_returns_typed_paths(self, tmp_path):
        cfg = AppConfig(
            assignment_name="HW1",
            output_dir=str(tmp_path),
            parsed_dir=str(tmp_path / "parsed"),
        )

        paths = get_assignment_output_paths(cfg)

        assert isinstance(paths, AssignmentOutputPaths)
        assert paths.output_dir == tmp_path
        assert paths.parsed_dir == tmp_path / "parsed"
        assert paths.solution_parsed == tmp_path / "solution_parsed.json"
        assert paths.graded_results == tmp_path / "graded_results.json"
        assert paths.gradescope_dir == tmp_path / "gradescope"

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

        assert isinstance(paths, AssignmentOutputPaths)
        assert paths.output_dir == tmp_path


class TestGradingQidNormalization:
    def test_grade_only_normalizes_q_prefix(self):
        cfg = ensure_app_config(
            {
                "assignment_name": "Demo",
                "grading": {"question_groups": [["1.1"]], "grade_only": ["Q1.1"]},
            }
        )
        assert cfg.grading.grade_only == ["1.1"]

    def test_question_groups_normalize_q_prefix(self):
        cfg = ensure_app_config(
            {
                "assignment_name": "Demo",
                "grading": {"question_groups": [["Q1.1", "q1.2"]]},
            }
        )
        assert cfg.grading.question_groups == [["1.1", "1.2"]]

    def test_invalid_qid_raises(self):
        with pytest.raises(ValueError):
            ensure_app_config(
                {
                    "assignment_name": "Demo",
                    "grading": {"question_groups": [["QX"]]},
                }
            )
