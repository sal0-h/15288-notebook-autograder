"""Shared helpers for joining canonical ``human_grades.csv`` to ``graded_results.json``.

Used by ``compare_experiment_to_human.py`` and ``analyze_human_ai_discrepancies.py``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from config_models import normalize_qid
from results_store import load_results

# assignment_name -> experiment_data/<cohort>/<lab>/human_grades.csv
HUMAN_CSV_REL: dict[str, tuple[str, str]] = {
    "S25_LabTest_2": ("S25", "LabTest_2"),
    "S25_LabTest_3": ("S25", "LabTest_3"),
    "S25_LabTest_4": ("S25", "LabTest_4"),
    "S25_LabTest_6": ("S25", "LabTest_6"),
    "S25_LabTest_7": ("S25", "LabTest_7"),
    "S26_LabTest_2": ("S26", "LabTest_2"),
    "S26_LabTest_1": ("S26", "LabTest_1"),
}

_META_LOWER = frozenset({"anon_id", "total score", "max points"})


def human_csv_path(assignment_name: str, *, project_root: Path) -> Path:
    if assignment_name not in HUMAN_CSV_REL:
        raise KeyError(
            f"No human_grades.csv mapping for assignment_name={assignment_name!r}"
        )
    cohort, lab = HUMAN_CSV_REL[assignment_name]
    return project_root / "experiment_data" / cohort / lab / "human_grades.csv"


def header_to_qid(header: str) -> str | None:
    h = (header or "").strip()
    if not h or h.strip().lower() in _META_LOWER:
        return None
    m = re.match(r"^Q?\s*(\d+)\.(\d+)", h, flags=re.I)
    if not m:
        return None
    try:
        return normalize_qid(f"{m.group(1)}.{m.group(2)}")
    except ValueError:
        return None


def to_float(x: object) -> float | None:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def graded_results_path(
    assignment_name: str,
    *,
    project_root: Path,
    model_tag: str | None,
    use_live: bool,
) -> Path:
    base = project_root / "output" / assignment_name
    if use_live:
        return base / "graded_results.json"
    if not model_tag:
        raise ValueError("Provide model_tag or use_live=True")
    return base / "experiment_runs" / model_tag / "graded_results.json"


def qid_columns_from_headers(fieldnames: list[str] | None) -> dict[str, str]:
    """Map canonical qid -> CSV column name."""
    out: dict[str, str] = {}
    for h in fieldnames or []:
        qid = header_to_qid(h)
        if qid:
            out[qid] = h
    return out


def load_ai_by_student(graded_path: Path) -> dict[str, Any]:
    """student_name -> GradedResult-like row (Pydantic model from results_store)."""
    ai_results = load_results(graded_path)
    return {r.student_name.strip(): r for r in ai_results if r.student_name}


def detect_grader_like_columns(fieldnames: list[str] | None) -> list[str]:
    """Heuristic: headers that might identify a human grader (raw Gradescope only)."""
    if not fieldnames:
        return []
    # "Autograder" contains the substring "graded" — exclude machine columns.
    needles = ("grader", "graded", "reviewer", "marker", "ta ", "ta_", "instructor")
    found: list[str] = []
    for h in fieldnames:
        low = (h or "").strip().lower()
        if "autograder" in low:
            continue
        if any(n in low for n in needles):
            found.append(h)
    return found
