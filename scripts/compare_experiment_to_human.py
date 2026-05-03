#!/usr/bin/env python3
"""Compare AI ``graded_results.json`` to canonical ``human_grades.csv`` per experiment lab.

Joins on ``anon_id`` (CSV) == ``student_name`` (graded JSON). Emits aggregate metrics
under ``experiment_analysis/<model_tag>/`` for paper tables.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts._human_ai_join import (
    HUMAN_CSV_REL,
    graded_results_path,
    human_csv_path,
    load_ai_by_student,
    qid_columns_from_headers,
    to_float,
)

DEFAULT_LABS: tuple[str, ...] = tuple(HUMAN_CSV_REL.keys())


def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / math.sqrt(sxx * syy)


def _mae(xs: list[float], ys: list[float]) -> float | None:
    if not xs:
        return None
    return sum(abs(a - b) for a, b in zip(xs, ys)) / len(xs)


def _rmse(xs: list[float], ys: list[float]) -> float | None:
    if not xs:
        return None
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(xs, ys)) / len(xs))


def analyze_lab(assignment_name: str, graded_path: Path) -> dict:
    human_path = human_csv_path(assignment_name, project_root=PROJECT_ROOT)
    if not human_path.is_file():
        return {
            "assignment_name": assignment_name,
            "error": f"missing human CSV: {human_path}",
        }
    if not graded_path.is_file():
        return {
            "assignment_name": assignment_name,
            "error": f"missing graded_results: {graded_path}",
        }

    with human_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        human_fields = list(reader.fieldnames or [])
        human_rows = list(reader)

    qid_columns = qid_columns_from_headers(human_fields)
    ai_by_student = load_ai_by_student(graded_path)

    total_h: list[float] = []
    total_a: list[float] = []
    per_q: dict[str, dict[str, float]] = {}

    missing_ai = 0
    skipped_total = 0

    for row in human_rows:
        aid = (row.get("anon_id") or "").strip()
        if not aid:
            continue
        ht = to_float(row.get("Total Score"))
        if ht is None:
            skipped_total += 1
            continue
        ai = ai_by_student.get(aid)
        if ai is None:
            missing_ai += 1
            continue
        total_h.append(ht)
        total_a.append(float(ai.total_score))

        for qid, col in qid_columns.items():
            hq = to_float(row.get(col))
            if hq is None:
                continue
            qd = ai.questions.get(qid)
            if qd is None:
                continue
            aq = float(qd.score)
            pq = per_q.setdefault(qid, {"sum_abs": 0.0, "sum_bias": 0.0, "n": 0})
            pq["sum_abs"] += abs(aq - hq)
            pq["sum_bias"] += aq - hq
            pq["n"] += 1

    per_q_out: dict[str, dict[str, float]] = {}
    for qid, pq in sorted(per_q.items(), key=lambda x: x[0]):
        n = int(pq["n"])
        if n == 0:
            continue
        per_q_out[qid] = {
            "n": float(n),
            "mae": pq["sum_abs"] / n,
            "bias_mean_ai_minus_human": pq["sum_bias"] / n,
        }

    out: dict = {
        "assignment_name": assignment_name,
        "human_csv": str(human_path),
        "graded_results": str(graded_path),
        "n_human_rows": len(human_rows),
        "n_ai_students": len(ai_by_student),
        "n_paired_total_score": len(total_h),
        "missing_ai_for_human_row": missing_ai,
        "skipped_missing_human_total": skipped_total,
        "total_score_mae": _mae(total_a, total_h),
        "total_score_rmse": _rmse(total_a, total_h),
        "total_score_pearson_r": _pearson_r(total_a, total_h),
        "per_question": per_q_out,
    }
    return out


def _write_csv_row(path: Path, row: dict[str, object], fieldnames: list[str]) -> None:
    write_header = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            w.writeheader()
        w.writerow(row)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare archived or live graded_results.json to human_grades.csv.",
    )
    parser.add_argument(
        "--model-tag",
        type=str,
        default=None,
        help="Folder name under output/<lab>/experiment_runs/<tag>/ (matches archived file's _provenance.model, sanitized)",
    )
    parser.add_argument(
        "--use-live-graded",
        action="store_true",
        help="Use output/<lab>/graded_results.json instead of experiment_runs archive.",
    )
    parser.add_argument(
        "--labs",
        type=str,
        default=",".join(DEFAULT_LABS),
        help="Comma-separated assignment_name values (default: all staged experiment labs).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for summary.json and per_lab.csv (default: experiment_analysis/<tag>).",
    )
    args = parser.parse_args()

    if args.use_live_graded and args.model_tag:
        print("Use only one of --use-live-graded or --model-tag.", file=sys.stderr)
        return 2
    if not args.use_live_graded and not args.model_tag:
        print("Provide --model-tag or --use-live-graded.", file=sys.stderr)
        return 2

    tag = args.model_tag or "live"
    out_dir = args.output_dir or (PROJECT_ROOT / "experiment_analysis" / tag)
    out_dir.mkdir(parents=True, exist_ok=True)

    labs = [x.strip() for x in args.labs.split(",") if x.strip()]
    per_lab_metrics: list[dict] = []

    for lab in labs:
        gp = graded_results_path(
            lab,
            project_root=PROJECT_ROOT,
            model_tag=args.model_tag,
            use_live=args.use_live_graded,
        )
        metrics = analyze_lab(lab, gp)
        per_lab_metrics.append(metrics)
        err = metrics.get("error")
        if err:
            print(f"{lab}: {err}", file=sys.stderr)
        else:
            r = metrics["total_score_pearson_r"]
            rs = f"{r:.4f}" if r is not None else "n/a"
            print(
                f"{lab}: paired={metrics['n_paired_total_score']} "
                f"MAE={metrics['total_score_mae']:.4f} r={rs}"
            )

    summary_path = out_dir / "summary.json"
    summary_path.write_text(
        json.dumps({"model_tag": tag, "labs": per_lab_metrics}, indent=2),
        encoding="utf-8",
    )

    csv_path = out_dir / "per_lab.csv"
    if csv_path.exists():
        csv_path.unlink()
    fields = [
        "assignment_name",
        "n_paired_total_score",
        "total_score_mae",
        "total_score_rmse",
        "total_score_pearson_r",
        "missing_ai_for_human_row",
        "error",
    ]
    for m in per_lab_metrics:
        row = {k: m.get(k) for k in fields}
        _write_csv_row(csv_path, row, fields)

    print(f"\nWrote {summary_path} and {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
