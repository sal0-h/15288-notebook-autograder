"""Unit tests for ``scripts/audit_experiment_output_rubrics.py``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _import_audit_module():
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    path = root / "scripts" / "audit_experiment_output_rubrics.py"
    spec = importlib.util.spec_from_file_location(
        "audit_experiment_output_rubrics", path
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_audit_passes_balanced_rubric() -> None:
    audit = _import_audit_module()
    cfg = {
        "assignment_name": "X",
        "grading": {"question_groups": [["1.1"], ["2.1", "2.2"]]},
        "rubrics": {
            "1.1": {"points": 4, "items": [{"deduction": 2}, {"deduction": 2}]},
            "2.1": {"points": 1, "items": [{"deduction": 1}]},
            "2.2": {"points": 3, "items": [{"deduction": 3}]},
        },
    }
    r = audit.audit_config_dict(cfg, coarse_min_deduction=5.0)
    assert r["structural_ok"] is True
    assert r["missing_rubric"] == []
    assert r["mismatch"] == []
    assert r["empty_items_pts_positive"] == []
    assert r["coarse_single_ge_threshold"] == []


def test_audit_detects_missing_and_mismatch() -> None:
    audit = _import_audit_module()
    cfg = {
        "assignment_name": "Y",
        "grading": {"question_groups": [["1.1"], ["2.1"]]},
        "rubrics": {
            "1.1": {"points": 4, "items": [{"deduction": 2}, {"deduction": 1}]},
        },
    }
    r = audit.audit_config_dict(cfg, coarse_min_deduction=5.0)
    assert r["structural_ok"] is False
    assert "2.1" in r["missing_rubric"]
    assert any(t[0] == "1.1" for t in r["mismatch"])


def test_audit_coarse_single_item() -> None:
    audit = _import_audit_module()
    cfg = {
        "assignment_name": "Z",
        "grading": {"question_groups": [["3.3"]]},
        "rubrics": {
            "3.3": {"points": 8, "items": [{"deduction": 8}]},
        },
    }
    r = audit.audit_config_dict(cfg, coarse_min_deduction=5.0)
    assert r["structural_ok"] is True
    assert r["coarse_single_ge_threshold"] == ["3.3"]
