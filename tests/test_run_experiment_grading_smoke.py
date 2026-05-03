"""Smoke tests for scripts/run_experiment_grading.py helpers."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from config_models import load_app_config


def _import_run_experiment_module():
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import importlib.util

    path = root / "scripts" / "run_experiment_grading.py"
    spec = importlib.util.spec_from_file_location("run_experiment_grading", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_sanitize_model_tag():
    reg = _import_run_experiment_module()
    assert reg._sanitize_model_tag("gpt-4.1-mini") == "gpt-4.1-mini"
    assert reg._sanitize_model_tag("gpt 5") == "gpt_5"


def _minimal_graded_row(*, student: str = "001", provenance_model: str) -> dict:
    return {
        "student_name": student,
        "total_score": 1.0,
        "total_max": 5.0,
        "questions": {
            "1.1": {
                "score": 1.0,
                "max": 5.0,
                "feedback": "",
                "confidence": "high",
                "requires_review": False,
                "_provenance": {
                    "model": provenance_model,
                    "rubric_hash": "abc",
                    "graded_at": "2026-01-01T00:00:00+00:00",
                },
            }
        },
    }


def test_archive_prior_uses_provenance_not_incoming_model(tmp_path):
    reg = _import_run_experiment_module()
    import logging

    log = logging.getLogger("test")
    out = tmp_path / "output" / "LabX"
    out.mkdir(parents=True)
    cfg_path = out / "config.yaml"
    sol = out / "solution.ipynb"
    sol.write_text("{}", encoding="utf-8")
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "assignment_name": "LabX",
                "model": "gpt-4.1-mini",
                "solution_notebook": str(sol),
                "rubrics": {},
                "grading": {"question_groups": [["1.1"]]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    graded = out / "graded_results.json"
    graded.write_text(
        json.dumps([_minimal_graded_row(student="001", provenance_model="gpt-4.1")]),
        encoding="utf-8",
    )

    cfg = load_app_config(cfg_path)
    dest = reg._archive_prior_graded_results(cfg, log)
    assert dest is not None
    assert dest == out / "experiment_runs" / "gpt-4.1" / "graded_results.json"
    assert dest.is_file()
    assert not graded.exists()


def test_archive_prior_skips_empty_list(tmp_path):
    reg = _import_run_experiment_module()
    import logging

    log = logging.getLogger("test")
    out = tmp_path / "output" / "LabX2"
    out.mkdir(parents=True)
    cfg_path = out / "config.yaml"
    sol = out / "solution.ipynb"
    sol.write_text("{}", encoding="utf-8")
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "assignment_name": "LabX2",
                "model": "gpt-4.1",
                "solution_notebook": str(sol),
                "rubrics": {},
                "grading": {"question_groups": [["1.1"]]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    graded = out / "graded_results.json"
    graded.write_text(json.dumps([]), encoding="utf-8")

    cfg = load_app_config(cfg_path)
    assert reg._archive_prior_graded_results(cfg, log) is None
    assert graded.is_file()


def test_infer_archive_tag_mixed_provenance_raises(tmp_path):
    reg = _import_run_experiment_module()
    import logging

    log = logging.getLogger("test")
    p = tmp_path / "g.json"
    p.write_text(
        json.dumps(
            [
                _minimal_graded_row(student="001", provenance_model="gpt-4.1"),
                _minimal_graded_row(student="002", provenance_model="gpt-4.1-mini"),
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="mixed"):
        reg._infer_archive_tag(p, fallback_model="gpt-4.1", log=log)


def test_run_one_lab_calls_parse_and_grade(tmp_path, monkeypatch):
    reg = _import_run_experiment_module()
    calls = {"parse": 0, "grade": 0}

    def fake_parse(c):
        calls["parse"] += 1
        return {"report": []}

    def fake_grade(c):
        calls["grade"] += 1
        yield {"status": "queue_info", "message": None}
        yield {"status": "usage", "cost_usd": 0}

    out = tmp_path / "output" / "LabY"
    out.mkdir(parents=True)
    cfg_path = out / "config.yaml"
    sol = out / "solution.ipynb"
    sol.write_text("{}", encoding="utf-8")
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "assignment_name": "LabY",
                "model": "gpt-4.1-mini",
                "solution_notebook": str(sol),
                "rubrics": {},
                "grading": {"question_groups": [["1.1"]]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(reg, "PROJECT_ROOT", tmp_path)
    with (
        patch.object(reg, "run_parse", fake_parse),
        patch.object(reg, "grade_all_students", fake_grade),
        patch.object(reg, "setup_assignment_logging", lambda *a, **k: None),
    ):
        err = reg._run_one_lab(
            "LabY",
            "gpt-4.1-mini",
            4,
            skip_parse=False,
            continue_on_error=True,
        )
    assert err == 0
    assert calls["parse"] == 1
    assert calls["grade"] == 1


def test_grading_complete_for_incoming(tmp_path):
    reg = _import_run_experiment_module()
    out = tmp_path / "output" / "LabC"
    out.mkdir(parents=True)
    parsed = out / "parsed"
    parsed.mkdir()
    (parsed / "001.json").write_text("{}", encoding="utf-8")
    (parsed / "002.json").write_text("{}", encoding="utf-8")
    cfg_path = out / "config.yaml"
    sol = out / "solution.ipynb"
    sol.write_text("{}", encoding="utf-8")
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "assignment_name": "LabC",
                "model": "gpt-4.1-mini",
                "solution_notebook": str(sol),
                "rubrics": {},
                "grading": {"question_groups": [["1.1"]]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    cfg = load_app_config(cfg_path)
    arch = out / "experiment_runs" / "gpt-4.1-mini" / "graded_results.json"
    arch.parent.mkdir(parents=True)
    arch.write_text(
        json.dumps(
            [
                _minimal_graded_row(student="001", provenance_model="gpt-4.1-mini"),
                _minimal_graded_row(student="002", provenance_model="gpt-4.1-mini"),
            ]
        ),
        encoding="utf-8",
    )
    assert reg._grading_complete_for_incoming(arch, cfg, "gpt-4.1-mini") is True
    assert reg._grading_complete_for_incoming(arch, cfg, "gpt-4.1") is False


def test_run_one_lab_skips_when_archive_complete(tmp_path, monkeypatch):
    reg = _import_run_experiment_module()
    calls = {"parse": 0, "grade": 0}

    def fake_parse(c):
        calls["parse"] += 1
        return {"report": []}

    def fake_grade(c):
        calls["grade"] += 1
        yield {"status": "queue_info", "message": None}

    out = tmp_path / "output" / "LabSkip"
    out.mkdir(parents=True)
    parsed = out / "parsed"
    parsed.mkdir()
    (parsed / "001.json").write_text("{}", encoding="utf-8")
    (parsed / "002.json").write_text("{}", encoding="utf-8")
    cfg_path = out / "config.yaml"
    sol = out / "solution.ipynb"
    sol.write_text("{}", encoding="utf-8")
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "assignment_name": "LabSkip",
                "model": "gpt-4.1-mini",
                "solution_notebook": str(sol),
                "rubrics": {},
                "grading": {"question_groups": [["1.1"]]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    arch = out / "experiment_runs" / "gpt-4.1-mini" / "graded_results.json"
    arch.parent.mkdir(parents=True)
    arch.write_text(
        json.dumps(
            [
                _minimal_graded_row(student="001", provenance_model="gpt-4.1-mini"),
                _minimal_graded_row(student="002", provenance_model="gpt-4.1-mini"),
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(reg, "PROJECT_ROOT", tmp_path)
    with (
        patch.object(reg, "run_parse", fake_parse),
        patch.object(reg, "grade_all_students", fake_grade),
        patch.object(reg, "setup_assignment_logging", lambda *a, **k: None),
    ):
        err = reg._run_one_lab(
            "LabSkip",
            "gpt-4.1-mini",
            4,
            skip_parse=False,
            continue_on_error=True,
        )
    assert err == 0
    assert calls["parse"] == 0
    assert calls["grade"] == 0


def test_run_one_lab_moves_complete_live_into_archive(tmp_path, monkeypatch):
    reg = _import_run_experiment_module()
    calls = {"grade": 0}

    def fake_grade(c):
        calls["grade"] += 1
        yield {"status": "queue_info", "message": None}

    out = tmp_path / "output" / "LabMove"
    out.mkdir(parents=True)
    parsed = out / "parsed"
    parsed.mkdir()
    (parsed / "a.json").write_text("{}", encoding="utf-8")
    (parsed / "b.json").write_text("{}", encoding="utf-8")
    cfg_path = out / "config.yaml"
    sol = out / "solution.ipynb"
    sol.write_text("{}", encoding="utf-8")
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "assignment_name": "LabMove",
                "model": "gpt-4.1",
                "solution_notebook": str(sol),
                "rubrics": {},
                "grading": {"question_groups": [["1.1"]]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    live = out / "graded_results.json"
    live.write_text(
        json.dumps(
            [
                _minimal_graded_row(student="a", provenance_model="gpt-4.1"),
                _minimal_graded_row(student="b", provenance_model="gpt-4.1"),
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(reg, "PROJECT_ROOT", tmp_path)
    with (
        patch.object(reg, "run_parse", lambda c: {"report": []}),
        patch.object(reg, "grade_all_students", fake_grade),
        patch.object(reg, "setup_assignment_logging", lambda *a, **k: None),
    ):
        err = reg._run_one_lab(
            "LabMove",
            "gpt-4.1",
            4,
            skip_parse=True,
            continue_on_error=True,
        )
    assert err == 0
    assert calls["grade"] == 0
    assert not live.exists()
    arch = out / "experiment_runs" / "gpt-4.1" / "graded_results.json"
    assert arch.is_file()
    data = json.loads(arch.read_text(encoding="utf-8"))
    assert len(data) == 2
