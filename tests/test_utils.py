"""Tests for utils.py: load_config, save_config, path resolution."""

import logging

from pathlib import Path

import pytest

from utils import (
    AppConfig,
    app_config_to_yaml_data,
    ensure_app_config,
    load_app_config,
    load_config,
    save_config,
    setup_assignment_logging,
)


class TestLoadConfig:
    def test_path_resolution_relative_to_config(self, tmp_path):
        config_path = tmp_path / "config.yaml"
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
        cfg = load_config(config_path)
        assert "solution_notebook" in cfg
        assert (
            "archive" in cfg["solution_notebook"]
            or "sol.ipynb" in cfg["solution_notebook"]
        )

    def test_assignment_scoping(self, tmp_path):
        config_path = tmp_path / "config.yaml"
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
        cfg = load_config(config_path)
        assert "LabTest_2" in cfg["output_dir"]
        assert "submissions" in cfg["submissions_dir"]
        assert "parsed" in cfg["parsed_dir"]

    def test_assignment_name_sanitization(self, tmp_path):
        config_path = tmp_path / "config.yaml"
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
        cfg = load_config(config_path)
        assert ".." not in cfg["output_dir"]
        assert "_" in cfg["output_dir"]


class TestSaveConfig:
    def test_round_trip(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
assignment_name: Test
model: gpt-5-mini
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
        cfg = load_config(config_path)
        save_config(cfg, config_path)
        cfg2 = load_config(config_path)
        assert cfg2.get("assignment_name") == cfg.get("assignment_name")
        assert "submissions" in str(cfg2.get("submissions_dir", ""))

    def test_save_config_accepts_appconfig(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        cfg = AppConfig(assignment_name="LabTest_3_S26")

        save_config(cfg, config_path)
        loaded = load_config(config_path)

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
        data = app_config_to_yaml_data(cfg)

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
            "model": "gpt-5-mini",
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
        root_cfg = load_config(config_path)
        assert root_cfg["assignment_name"] == "Lab #1"

    def test_save_config_with_explicit_assignment_config_path(self, tmp_path):
        assignment_config_path = tmp_path / "output" / "LabTest_3_S26" / "config.yaml"
        (tmp_path / "archive").mkdir(parents=True)
        (tmp_path / "archive" / "sol.ipynb").write_text("{}", encoding="utf-8")

        cfg = {
            "assignment_name": "LabTest_3_S26",
            "model": "gpt-5-mini",
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
        assert "model: gpt-5-mini" in assignment_cfg_text
        assert "solution_notebook:" in assignment_cfg_text
        assert "parsing:" in assignment_cfg_text
        assert "grading:" in assignment_cfg_text

        root_cfg_text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
        assert "assignment_name: LabTest_3_S26" in root_cfg_text
        assert "rubric_review:" in root_cfg_text

    def test_save_load_preserves_grade_flags_and_review_controls(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        (tmp_path / "archive").mkdir(parents=True)
        (tmp_path / "archive" / "sol.ipynb").write_text("{}", encoding="utf-8")

        cfg = {
            "assignment_name": "LabTest_3_S26",
            "model": "gpt-5-mini",
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
        loaded = load_config(config_path)

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
