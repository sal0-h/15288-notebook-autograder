"""Smoke tests for scripts/compute_irr.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def tiny_hw1(tmp_path: Path) -> tuple[Path, Path]:
    manual = tmp_path / "gradescope"
    manual.mkdir()
    gs = {
        "tests": [
            {"name": "1.1", "score": 1.0, "max_score": 1.0},
            {"name": "1.2", "score": 2.0, "max_score": 3.0},
        ]
    }
    (manual / "Test Student_hw1_tasks.json").write_text(
        json.dumps(gs), encoding="utf-8"
    )
    graded = [
        {
            "student_name": "Test Student",
            "questions": {
                "1.1": {"score": 1.0, "max": 1.0},
                "1.2": {"score": 2.5, "max": 3.0},
            },
            "total_score": 3.5,
            "total_max": 4.0,
        }
    ]
    gp = tmp_path / "graded_results.json"
    gp.write_text(json.dumps(graded), encoding="utf-8")
    return manual, gp


def test_compute_irr_writes_metrics(tiny_hw1, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "compute_irr.py"
    manual, graded = tiny_hw1
    out_json = tmp_path / "irr.json"
    r = subprocess.run(
        [
            sys.executable,
            str(script),
            "--manual-dir",
            str(manual),
            "--graded",
            str(graded),
            "--no-grader_b-csv",
            "--out",
            str(out_json),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["n_pairs"] == 2
    assert data["n_students"] == 1
    assert "cohen_kappa_unweighted" in data
    assert data["students"] == ["Test Student"]
    assert data["human_human_salman_vs_grader_b"] is None


def test_compute_irr_two_graders_human_human_and_split_ai(tiny_hw1, tmp_path: Path):
    """Grader B CSV matches Salman JSON on 1.1 but differs on 1.2."""
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "compute_irr.py"
    manual, graded = tiny_hw1
    grader_b = tmp_path / "grader_b.csv"
    grader_b.write_text(
        "student_name,total_score,1.1,1.2\nTest Student,9,1,4\n",
        encoding="utf-8",
    )
    out_json = tmp_path / "irr2.json"
    r = subprocess.run(
        [
            sys.executable,
            str(script),
            "--manual-dir",
            str(manual),
            "--graded",
            str(graded),
            "--grader_b-csv",
            str(grader_b),
            "--out",
            str(out_json),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["n_pairs"] == 2
    hh = data["human_human_salman_vs_grader_b"]
    assert hh is not None
    assert hh["n_pairs"] == 2
    assert hh["mae"] == pytest.approx(1.0)  # |1-1| + |2-4| over 2
    assert data["human_ai_salman_gradescope_json"]["mae"] == pytest.approx(0.25)
    assert data["human_ai_grader_b_wide_csv"]["mae"] == pytest.approx(0.75)
