"""Tests for config_models: load, save, path resolution, validation."""

import json
from pathlib import Path

import pytest
import yaml

from config_models import (
    AppConfig,
    GradingConfig,
    ensure_app_config,
    load_app_config,
    load_solution_parsed,
    normalize_qid,
    save_config,
)


class TestNormalizeQid:
    def test_strips_q_prefix(self):
        assert normalize_qid("Q1.1") == "1.1"
        assert normalize_qid("q2.3") == "2.3"

    def test_already_normalized(self):
        assert normalize_qid("1.1") == "1.1"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            normalize_qid("abc")
        with pytest.raises(ValueError):
            normalize_qid("1")


class TestSaveLoadRoundTrip:
    def test_save_and_load(self, tmp_path):
        config_path = tmp_path / "output" / "test" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        cfg = AppConfig(assignment_name="test")
        save_config(cfg, config_path)
        assert config_path.exists()
        loaded = load_app_config(config_path)
        assert loaded.assignment_name == "test"

    def test_load_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_app_config(tmp_path / "nonexistent.yaml")


class TestGradingConfig:
    def test_effective_groups_no_grade_only(self):
        gc = GradingConfig(question_groups=[["1.1", "1.2"], ["2.1"]])
        assert gc.get_effective_groups() == [["1.1", "1.2"], ["2.1"]]

    def test_effective_groups_with_grade_only(self):
        gc = GradingConfig(question_groups=[["1.1", "1.2"], ["2.1"]], grade_only=["1.1"])
        groups = gc.get_effective_groups()
        assert groups == [["1.1"]]

    def test_grade_only_merge_requires_grade_only(self):
        with pytest.raises(Exception):
            GradingConfig(question_groups=[["1.1"]], grade_only_merge=True)


class TestLoadSolutionParsed:
    def test_loads_existing(self, tmp_path):
        config_path = tmp_path / "output" / "test" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        cfg = AppConfig(
            assignment_name="test",
            output_dir=str(config_path.parent),
            parsed_dir=str(config_path.parent / "parsed"),
        )
        solution = {"sections": {"1": {"questions": {"1.1": {"points": 5}}}}}
        (config_path.parent / "solution_parsed.json").write_text(json.dumps(solution))
        result = load_solution_parsed(cfg)
        assert "1" in result["sections"]

    def test_missing_raises(self, tmp_path):
        cfg = AppConfig(
            assignment_name="test",
            output_dir=str(tmp_path),
            parsed_dir=str(tmp_path / "parsed"),
        )
        with pytest.raises(FileNotFoundError):
            load_solution_parsed(cfg)
