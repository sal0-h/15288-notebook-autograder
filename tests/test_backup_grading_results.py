"""Tests for scripts/backup_grading_results.py."""

import importlib.util
import json
from pathlib import Path

import pytest


def _load_backup_module():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "backup_grading_results",
        root / "scripts" / "backup_grading_results.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fake_project(tmp_path):
    root = tmp_path / "proj"
    (root / "output" / "LabA").mkdir(parents=True)
    (root / "output" / "LabA" / "graded_results.json").write_text(
        json.dumps([{"student_name": "001", "total_score": 1, "total_max": 5}]),
        encoding="utf-8",
    )
    er = root / "output" / "LabA" / "experiment_runs" / "gpt-4.1"
    er.mkdir(parents=True)
    (er / "graded_results.json").write_text("[]", encoding="utf-8")
    (root / "output" / "LabEmpty").mkdir()
    return root


def test_backup_grading_results_copies_files(fake_project, tmp_path):
    mod = _load_backup_module()
    dest = tmp_path / "bk"
    lines = mod.backup_grading_results(
        fake_project, dest, include_experiment_analysis=False
    )
    assert any("LabA/graded_results.json" in ln for ln in lines)
    assert (dest / "output" / "LabA" / "graded_results.json").is_file()
    assert (
        dest / "output" / "LabA" / "experiment_runs" / "gpt-4.1" / "graded_results.json"
    ).is_file()
    assert (dest / "MANIFEST.txt").is_file()
