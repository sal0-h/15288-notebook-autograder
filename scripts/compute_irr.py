#!/usr/bin/env python3
"""Align HW1 manual scores from two graders to AI ``graded_results.json``.

**Grader A (Salman):** per-question scores from Gradescope ``tests`` JSON under
``output/HW1_Manual_Grading/gradescope/*_hw1_tasks*.json`` (or a tracked copy).

**Grader B (Grader B):** optional wide CSV
(``research/paper/hw1_manual_grades_grader_b_wide.csv`` when present) with
``student_name`` and ``1.1`` / ``Q1.1`` style columns. The CSV is a *second
rater*, not an overlay: when both sources exist, the script reports (1)
human–human agreement on the same (student, question) pairs, (2) human–AI for
each grader separately.

Emits Cohen's kappa (unweighted), quadratic-weighted kappa, ICC(2,1) (two-way
random, single measure, absolute agreement), Pearson r, and MAE on each paired
vector.

Usage:
    python scripts/compute_irr.py
    python scripts/compute_irr.py --out research/paper/hw1_irr_metrics.json
    python scripts/compute_irr.py --no-grader_b-csv   # Gradescope JSON (Salman) only
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml
from sklearn.metrics import cohen_kappa_score

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_models import normalize_qid
from results_store import load_results

_QID_RE = re.compile(r"^(\d+)\.(\d+)$")

GRADER_A_NAME = "Salman"
GRADER_A_ROLE = "gradescope_json"
GRADER_B_NAME = "Grader B"
GRADER_B_ROLE = "wide_csv"


def _student_name_from_manual_path(path: Path) -> str | None:
    """Map ``.../Student 001_hw1_tasks.json`` → ``Student 001``."""
    stem = path.stem
    for sep in ("_hw1_tasks", " hw1_tasks"):
        if sep in stem:
            base = stem.split(sep)[0].strip()
            break
    else:
        base = stem
    base = re.sub(r"_mrri$", "", base, flags=re.I).strip()
    base = " ".join(base.replace("_", " ").split())
    return base or None


def _load_human_tests(path: Path) -> dict[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tests = data.get("tests") or []
    out: dict[str, float] = {}
    for t in tests:
        name = (t.get("name") or "").strip()
        if not name or name.lower().startswith("notebook format"):
            continue
        m = _QID_RE.match(name)
        if not m:
            continue
        qid = normalize_qid(f"{m.group(1)}.{m.group(2)}")
        sc = t.get("score")
        if sc is None:
            continue
        out[qid] = float(sc)
    return out


def load_human_gradescope(
    manual_dir: Path,
) -> tuple[dict[tuple[str, str], float], list[str]]:
    """``(student, qid) -> score`` from Gradescope-style JSON files."""
    human: dict[tuple[str, str], float] = {}
    missing: list[str] = []
    if not manual_dir.is_dir():
        return human, missing
    for path in sorted(manual_dir.glob("*.json")):
        if "_hw1_tasks" not in path.stem and " hw1_tasks" not in path.stem:
            continue
        student = _student_name_from_manual_path(path)
        if not student:
            missing.append(path.name)
            continue
        for qid, h_score in _load_human_tests(path).items():
            human[(student, qid)] = float(h_score)
    return human, missing


def _csv_column_to_qid(col: str) -> str | None:
    """Map ``1.10`` / ``Q1.10`` / ``q2.5`` header cells to canonical ``1.10``."""
    c = col.strip()
    if not c or c.lower() in ("student_name", "total_score"):
        return None
    if c.upper().startswith("Q") and len(c) > 1 and c[1].isdigit():
        c = c[1:]
    m = _QID_RE.match(c.replace(" ", ""))
    if not m:
        return None
    return normalize_qid(f"{m.group(1)}.{m.group(2)}")


def _load_grader_b_csv(path: Path) -> dict[tuple[str, str], float]:
    """``(student_name, qid) -> score`` from a wide CSV; empty cells skipped."""
    out: dict[tuple[str, str], float] = {}
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return out
        qid_cols: list[tuple[str, str]] = []
        for col in reader.fieldnames:
            qid = _csv_column_to_qid(col)
            if qid:
                qid_cols.append((col, qid))
        for row in reader:
            st = (row.get("student_name") or "").strip()
            if not st:
                continue
            for col, qid in qid_cols:
                raw = (row.get(col) or "").strip()
                if raw == "":
                    continue
                out[(st, qid)] = float(raw)
    return out


def load_human_grader_b_csvs(
    paths: list[Path],
) -> tuple[dict[tuple[str, str], float], list[str]]:
    merged: dict[tuple[str, str], float] = {}
    missing: list[str] = []
    for p in paths:
        if not p.is_file():
            missing.append(f"missing Grader B CSV: {p}")
            continue
        for k, v in _load_grader_b_csv(p).items():
            merged[k] = float(v)
    return merged, missing


def _icc_2_1_absolute(data: np.ndarray) -> float:
    """Two-way random, single measure, absolute agreement (McGraw & Wong ICC(2,1))."""
    y = np.asarray(data, dtype=float)
    n, k = y.shape
    if n < 2 or k < 2:
        return float("nan")
    grand = y.mean()
    mean_s = y.mean(axis=1)
    msb = k * np.sum((mean_s - grand) ** 2) / (n - 1)
    msw = np.sum((y - mean_s[:, None]) ** 2) / (n * (k - 1))
    if msb + (k - 1) * msw == 0:
        return float("nan")
    return (msb - msw) / (msb + (k - 1) * msw)


def _pearson_r(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2:
        return float("nan")
    aa, bb = np.asarray(a, float), np.asarray(b, float)
    if np.std(aa) == 0 or np.std(bb) == 0:
        return float("nan")
    return float(np.corrcoef(aa, bb)[0, 1])


def _mae(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


def _encode_for_kappa(h: np.ndarray, a: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Map rounded scores to consecutive integer labels for sklearn kappa."""
    h_r = np.round(h, 6)
    a_r = np.round(a, 6)
    vals = sorted(set(h_r.tolist()) | set(a_r.tolist()))
    idx = {v: i for i, v in enumerate(vals)}
    return np.array([idx[float(x)] for x in h_r]), np.array(
        [idx[float(x)] for x in a_r]
    )


def pair_human_ai(
    human: dict[tuple[str, str], float],
    graded_path: Path,
) -> tuple[list[dict[str, object]], list[str]]:
    ai_by = {
        r.student_name.strip(): r for r in load_results(graded_path) if r.student_name
    }
    rows: list[dict[str, object]] = []
    missing: list[str] = []
    for (student, qid), h_score in sorted(human.items()):
        ai_row = ai_by.get(student)
        if ai_row is None:
            missing.append(f"no AI row for {student!r} (qid {qid})")
            continue
        q = ai_row.questions.get(qid)
        if q is None:
            continue
        rows.append(
            {
                "student": student,
                "qid": qid,
                "human": float(h_score),
                "ai": float(q.score),
                "ai_max": float(q.max),
            }
        )
    return rows, missing


def pair_human_human(
    salman: dict[tuple[str, str], float],
    grader_b: dict[tuple[str, str], float],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in sorted(set(salman) & set(grader_b)):
        student, qid = key
        rows.append(
            {
                "student": student,
                "qid": qid,
                "human_salman_json": float(salman[key]),
                "human_grader_b_csv": float(grader_b[key]),
            }
        )
    return rows


def compute_agreement_metrics(
    rows: list[dict[str, object]], *, left_key: str, right_key: str
) -> dict[str, object]:
    if not rows:
        return {"error": "no paired scores"}
    h = np.array([float(r[left_key]) for r in rows], dtype=float)
    a = np.array([float(r[right_key]) for r in rows], dtype=float)
    y_h, y_a = _encode_for_kappa(h, a)
    kappa = float(cohen_kappa_score(y_h, y_a))
    qwk = float(cohen_kappa_score(y_h, y_a, weights="quadratic"))
    icc = _icc_2_1_absolute(np.column_stack([h, a]))
    return {
        "n_pairs": len(rows),
        "n_students": len({r["student"] for r in rows}),
        "cohen_kappa_unweighted": kappa,
        "cohen_kappa_quadratic_weighted": qwk,
        "icc_2_1_absolute": icc,
        "pearson_r": _pearson_r(h, a),
        "mae": _mae(h, a),
        "rmse": float(math.sqrt(np.mean((h - a) ** 2))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manual-dir",
        type=Path,
        default=PROJECT_ROOT / "output" / "HW1_Manual_Grading" / "gradescope",
    )
    parser.add_argument(
        "--graded",
        type=Path,
        default=PROJECT_ROOT / "output" / "HW1" / "graded_results.json",
    )
    parser.add_argument(
        "--grader_b-csv",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help=(
            "Grader B's wide CSV (student_name + per-question columns). Repeatable; "
            "later files overwrite same (student,qid). "
            "Default: research/paper/hw1_manual_grades_grader_b_wide.csv if it exists."
        ),
    )
    parser.add_argument(
        "--staff-csv",
        type=Path,
        action="append",
        default=[],
        metavar="PATH",
        help="Deprecated alias for --grader_b-csv.",
    )
    parser.add_argument(
        "--no-grader_b-csv",
        action="store_true",
        help="Do not load the default Grader B wide CSV.",
    )
    parser.add_argument(
        "--no-staff-csv",
        action="store_true",
        help="Deprecated alias for --no-grader_b-csv.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON summary (default: print only)",
    )
    parser.add_argument(
        "--include-pairs",
        action="store_true",
        help="Include per-(student,qid) rows for human–human and each human–AI block.",
    )
    args = parser.parse_args()

    grader_b_paths: list[Path] = []
    if args.grader_b_csv or args.staff_csv:
        raw = list(args.grader_b_csv) + list(args.staff_csv)
        grader_b_paths = [p if p.is_absolute() else PROJECT_ROOT / p for p in raw]
    elif not args.no_grader_b_csv and not args.no_staff_csv:
        auto = (
            PROJECT_ROOT / "research" / "paper" / "hw1_manual_grades_grader_b_wide.csv"
        )
        if auto.is_file():
            grader_b_paths = [auto]

    manual_ok = args.manual_dir.is_dir()
    if not manual_ok and not grader_b_paths:
        print(
            f"Missing manual dir and no Grader B CSV: {args.manual_dir}",
            file=sys.stderr,
        )
        return 2
    if not args.graded.is_file():
        print(f"Missing graded_results: {args.graded}", file=sys.stderr)
        return 2

    salman_h, miss_a = load_human_gradescope(args.manual_dir)
    grader_b_h, miss_b = load_human_grader_b_csvs(grader_b_paths)
    parse_warnings = miss_a + miss_b

    rows_salman_ai, miss_sa = pair_human_ai(salman_h, args.graded)
    rows_grader_b_ai, miss_ya = pair_human_ai(grader_b_h, args.graded)
    parse_warnings.extend(miss_sa)
    parse_warnings.extend(miss_ya)

    rows_hh = (
        pair_human_human(salman_h, grader_b_h) if (salman_h and grader_b_h) else []
    )

    m_salman = compute_agreement_metrics(
        rows_salman_ai, left_key="human", right_key="ai"
    )
    m_grader_b = compute_agreement_metrics(
        rows_grader_b_ai, left_key="human", right_key="ai"
    )
    m_hh = (
        compute_agreement_metrics(
            rows_hh, left_key="human_salman_json", right_key="human_grader_b_csv"
        )
        if rows_hh
        else {"error": "no overlapping human–human (student, question) pairs"}
    )

    cfg_path = PROJECT_ROOT / "output" / "HW1" / "config.yaml"
    grading_model = None
    if cfg_path.is_file():
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
            grading_model = cfg.get("model")
        except Exception:
            grading_model = None

    out: dict[str, object] = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "assignment": "HW1",
        "ai_model_from_config_yaml": grading_model,
        "grader_a": {
            "display_name": GRADER_A_NAME,
            "role": GRADER_A_ROLE,
            "paths": [str(args.manual_dir)],
        },
        "grader_b": (
            {
                "display_name": GRADER_B_NAME,
                "role": GRADER_B_ROLE,
                "paths": [str(p) for p in grader_b_paths],
            }
            if grader_b_paths
            else None
        ),
        "manual_dir": str(args.manual_dir),
        "grader_b_csv": [str(p) for p in grader_b_paths],
        "graded_results": str(args.graded),
        "human_human_salman_vs_grader_b": (
            None if "error" in m_hh else {k: v for k, v in m_hh.items()}
        ),
        "human_ai_salman_gradescope_json": (
            None if "error" in m_salman else {k: v for k, v in m_salman.items()}
        ),
        "human_ai_grader_b_wide_csv": (
            None if "error" in m_grader_b else {k: v for k, v in m_grader_b.items()}
        ),
        "human_human_note": (
            "Two instructors (Salman: Gradescope JSON; Grader B: wide CSV) graded the "
            "same five-student subsample; human–human κ is on paired per-question scores."
            if rows_hh
            else (
                "Only one human source is present; load Grader B's CSV (or pass "
                "--grader_b-csv) to obtain human–human agreement on the same subsample."
            )
        ),
        "parse_warnings": parse_warnings,
    }

    # Back-compat: top-level mirrors Salman vs AI when that block exists
    if "error" not in m_salman:
        for k, v in m_salman.items():
            out[k] = v
        out["students"] = sorted({r["student"] for r in rows_salman_ai})
    elif "error" not in m_grader_b:
        for k, v in m_grader_b.items():
            out[k] = v
        out["students"] = sorted({r["student"] for r in rows_grader_b_ai})
    else:
        out["students"] = []

    if args.include_pairs:
        out["per_question_pairs_salman_ai"] = rows_salman_ai
        out["per_question_pairs_grader_b_ai"] = rows_grader_b_ai
        out["per_question_pairs_human_human"] = rows_hh

    text = json.dumps(out, indent=2)
    fatal = "error" in m_salman and "error" in m_grader_b

    if args.out:
        outp = args.out if args.out.is_absolute() else PROJECT_ROOT / args.out
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(text + "\n", encoding="utf-8")
        print(f"Wrote {outp}", file=sys.stderr)
    else:
        slim = {k: v for k, v in out.items() if not k.startswith("per_question_pairs")}
        print(json.dumps(slim, indent=2))
    return 0 if not fatal else 1


if __name__ == "__main__":
    raise SystemExit(main())
