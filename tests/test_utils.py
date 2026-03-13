"""Tests for utils.py: load_config, save_config, path resolution."""

import logging

from pathlib import Path

import pytest

from utils import load_config, save_config, setup_assignment_logging


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
prompts:
  system: "Grade"
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
prompts:
  system: "Grade"
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
prompts:
  system: "Grade"
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
prompts:
  system: "Grade"
""",
            encoding="utf-8",
        )
        (tmp_path / "sol.ipynb").write_text("{}", encoding="utf-8")
        cfg = load_config(config_path)
        save_config(cfg, config_path)
        cfg2 = load_config(config_path)
        assert cfg2.get("assignment_name") == cfg.get("assignment_name")
        assert "submissions" in str(cfg2.get("submissions_dir", ""))

    def test_save_config_preserves_hash_in_assignment_name(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
assignment_name: default
output_dir: output
prompts:
  system: "Grade"
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
            "prompts": {"system": "Grade"},
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
            "prompts": {"system": "Grade"},
        }

        save_config(cfg, assignment_config_path)
        assignment_cfg_text = assignment_config_path.read_text(encoding="utf-8")
        assert "model: gpt-5-mini" in assignment_cfg_text
        assert "solution_notebook:" in assignment_cfg_text
        assert "parsing:" in assignment_cfg_text
        assert "grading:" in assignment_cfg_text

        root_cfg_text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
        assert "assignment_name: LabTest_3_S26" in root_cfg_text
        assert "prompts:" in root_cfg_text

    class TestSetupAssignmentLogging:
        def test_switches_autograder_log_file(self, tmp_path):
            root = logging.getLogger()
            old_handlers = list(root.handlers)
            for handler in list(root.handlers):
                if isinstance(handler, logging.FileHandler):
                    root.removeHandler(handler)
                    handler.close()

            try:
                first = setup_assignment_logging(tmp_path / "one")
                second = setup_assignment_logging(tmp_path / "two")
                file_handlers = [
                    h for h in root.handlers if isinstance(h, logging.FileHandler)
                ]

                assert first.name == "autograder.log"
                assert second.name == "autograder.log"
                assert len(file_handlers) == 1
                assert Path(file_handlers[0].baseFilename).resolve() == second.resolve()
            finally:
                for handler in list(root.handlers):
                    if isinstance(handler, logging.FileHandler):
                        root.removeHandler(handler)
                        handler.close()
                for handler in old_handlers:
                    if handler not in root.handlers:
                        root.addHandler(handler)
