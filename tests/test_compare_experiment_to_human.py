"""Tests for scripts/compare_experiment_to_human.py metrics."""

import json
import sys
from pathlib import Path


def _import_compare_module():
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import importlib.util

    path = root / "scripts" / "compare_experiment_to_human.py"
    spec = importlib.util.spec_from_file_location("compare_experiment_to_human", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_analyze_lab_perfect_agreement(tmp_path, monkeypatch):
    cmp = _import_compare_module()
    human = tmp_path / "human.csv"
    human.write_text(
        "anon_id,Total Score,Max Points,1.1\n"
        "001,6,10,3\n"
        "002,10,10,5\n"
        "003,14,10,7\n",
        encoding="utf-8",
    )
    graded = tmp_path / "graded.json"
    graded.write_text(
        json.dumps(
            [
                {
                    "student_name": "001",
                    "total_score": 7.0,
                    "total_max": 10.0,
                    "questions": {
                        "1.1": {
                            "score": 4.0,
                            "max": 5.0,
                            "feedback": "",
                            "confidence": "high",
                            "requires_review": False,
                        }
                    },
                },
                {
                    "student_name": "002",
                    "total_score": 11.0,
                    "total_max": 10.0,
                    "questions": {
                        "1.1": {
                            "score": 6.0,
                            "max": 5.0,
                            "feedback": "",
                            "confidence": "high",
                            "requires_review": False,
                        }
                    },
                },
                {
                    "student_name": "003",
                    "total_score": 15.0,
                    "total_max": 10.0,
                    "questions": {
                        "1.1": {
                            "score": 8.0,
                            "max": 5.0,
                            "feedback": "",
                            "confidence": "high",
                            "requires_review": False,
                        }
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cmp,
        "human_csv_path",
        lambda assignment_name, *, project_root: human,
    )
    m = cmp.analyze_lab("AnyLab", graded)
    assert m.get("human_total_is_constant") is False
    assert m["n_paired_total_score"] == 3
    assert m["total_score_mae"] == 1.0
    assert m["total_score_pearson_r"] is not None
    assert abs(m["total_score_pearson_r"] - 1.0) < 1e-9
    assert m["per_question"]["1.1"]["mae"] == 1.0


def test_analyze_lab_constant_human_total(tmp_path, monkeypatch):
    """S23-style placeholder CSV: all human totals identical → Pearson r undefined."""
    cmp = _import_compare_module()
    human = tmp_path / "human.csv"
    human.write_text(
        "anon_id,Total Score,Max Points,1.1\n"
        "001,0,100,\n"
        "002,0,100,\n",
        encoding="utf-8",
    )
    graded = tmp_path / "graded.json"
    graded.write_text(
        json.dumps(
            [
                {
                    "student_name": "001",
                    "total_score": 50.0,
                    "total_max": 100.0,
                    "questions": {},
                },
                {
                    "student_name": "002",
                    "total_score": 60.0,
                    "total_max": 100.0,
                    "questions": {},
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cmp,
        "human_csv_path",
        lambda assignment_name, *, project_root: human,
    )
    m = cmp.analyze_lab("PlaceholderLab", graded)
    assert m.get("error") is None
    assert m["human_total_is_constant"] is True
    assert m["total_score_pearson_r"] is None
    assert m["total_score_mae"] == 55.0  # mean |50-0|, |60-0|


def test_pearson_hand_computed():
    cmp = _import_compare_module()
    xs = [1.0, 2.0, 3.0]
    ys = [2.0, 4.0, 6.0]
    r = cmp._pearson_r(xs, ys)
    assert r is not None and abs(r - 1.0) < 1e-9
