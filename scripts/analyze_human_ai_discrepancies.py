#!/usr/bin/env python3
"""Human–AI discrepancy report (Markdown): extreme disagreements, quartile MAE, case study.

Joins ``human_grades.csv`` to ``graded_results.json`` like ``compare_experiment_to_human.py``.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from config_models import ensure_app_config
from scripts._human_ai_join import (
    HUMAN_CSV_REL,
    detect_grader_like_columns,
    graded_results_path,
    human_csv_path,
    load_ai_by_student,
    qid_columns_from_headers,
    to_float,
)

DEFAULT_LABS: tuple[str, ...] = tuple(HUMAN_CSV_REL.keys())
TASK5_LAB = "S25_LabTest_7"
TASK5_QID = "1.21"
TASK5_STUDENTS = ("001", "003")


def _is_zero(x: float | None) -> bool:
    return x is not None and abs(float(x)) < 1e-9


def _quartile_index_slices(n: int) -> tuple[slice, slice, slice]:
    """Sorted-by-human order: bottom 25%, middle 50%, top 25% (integer split)."""
    if n <= 0:
        raise ValueError("n must be positive")
    lo = n // 4
    hi = n // 4
    return slice(0, lo), slice(lo, n - hi), slice(n - hi, n)


def _task4_tier_stats(
    human_totals: list[float], ai_totals: list[float]
) -> list[dict[str, Any]]:
    """Return three rows: bottom / middle / top quartiles by human total."""
    n = len(human_totals)
    if n != len(ai_totals):
        raise ValueError("length mismatch")
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: human_totals[i])
    s0, s1, s2 = _quartile_index_slices(n)
    tiers = [
        ("Bottom 25% (by human total)", order[s0]),
        ("Middle 50%", order[s1]),
        ("Top 25% (by human total)", order[s2]),
    ]
    out: list[dict[str, Any]] = []
    for label, idxs in tiers:
        if not idxs:
            out.append(
                {
                    "tier": label,
                    "n": 0,
                    "mean_abs_delta_total": None,
                    "median_abs_delta_total": None,
                }
            )
            continue
        deltas = [abs(ai_totals[i] - human_totals[i]) for i in idxs]
        out.append(
            {
                "tier": label,
                "n": len(idxs),
                "mean_abs_delta_total": sum(deltas) / len(deltas),
                "median_abs_delta_total": float(statistics.median(deltas)),
            }
        )
    return out


def _load_paired_question_matrix(
    assignment_name: str,
    *,
    project_root: Path,
    graded_path: Path,
) -> tuple[
    list[str],
    dict[str, str],
    dict[str, dict[str, tuple[float | None, float | None, float, float]]],
    str | None,
]:
    """Returns (anon_ids in CSV order, qid_columns, data[anon][qid]=(human, ai, ai_max, ai_score), error)."""
    human_path = human_csv_path(assignment_name, project_root=project_root)
    if not human_path.is_file():
        return [], {}, {}, f"missing human CSV: {human_path}"
    if not graded_path.is_file():
        return [], {}, {}, f"missing graded_results: {graded_path}"

    with human_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        human_fields = list(reader.fieldnames or [])
        human_rows = list(reader)
    qid_columns = qid_columns_from_headers(human_fields)
    ai_by_student = load_ai_by_student(graded_path)

    matrix: dict[str, dict[str, tuple[float | None, float | None, float, float]]] = {}
    anons: list[str] = []
    for row in human_rows:
        aid = (row.get("anon_id") or "").strip()
        if not aid:
            continue
        ai = ai_by_student.get(aid)
        if ai is None:
            continue
        anons.append(aid)
        per_q: dict[str, tuple[float | None, float | None, float, float]] = {}
        for qid, col in qid_columns.items():
            hq = to_float(row.get(col))
            qd = ai.questions.get(qid)
            if qd is None:
                per_q[qid] = (hq, None, 0.0, 0.0)
            else:
                per_q[qid] = (hq, float(qd.score), float(qd.max), float(qd.score))
        matrix[aid] = per_q

    return anons, qid_columns, matrix, None


def _task2_rows(
    matrix: dict[str, dict[str, tuple[float | None, float | None, float, float]]],
    anons: list[str],
    qid_columns: dict[str, str],
    over_frac: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for aid in anons:
        for qid in qid_columns:
            hq, ai_score, ai_max, _ = matrix[aid][qid]
            if hq is None or ai_score is None:
                continue
            if not _is_zero(hq):
                continue
            if ai_max <= 0:
                continue
            if ai_score > over_frac * ai_max:
                rows.append(
                    {
                        "anon_id": aid,
                        "qid": qid,
                        "human": hq,
                        "ai_score": ai_score,
                        "ai_max": ai_max,
                    }
                )
    return rows


def _task3_rows(
    matrix: dict[str, dict[str, tuple[float | None, float | None, float, float]]],
    anons: list[str],
    qid_columns: dict[str, str],
    under_frac: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for aid in anons:
        for qid in qid_columns:
            hq, ai_score, ai_max, _ = matrix[aid][qid]
            if hq is None or ai_score is None:
                continue
            if not _is_zero(ai_score):
                continue
            if ai_max <= 0:
                continue
            if hq > under_frac * ai_max:
                rows.append(
                    {
                        "anon_id": aid,
                        "qid": qid,
                        "human": hq,
                        "ai_score": ai_score,
                        "ai_max": ai_max,
                    }
                )
    return rows


def _paired_totals_for_lab(
    assignment_name: str,
    *,
    project_root: Path,
    graded_path: Path,
) -> tuple[list[float], list[float], str | None]:
    human_path = human_csv_path(assignment_name, project_root=project_root)
    if not human_path.is_file() or not graded_path.is_file():
        return [], [], None
    with human_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        human_rows = list(reader)
    ai_by_student = load_ai_by_student(graded_path)
    h_list: list[float] = []
    a_list: list[float] = []
    for row in human_rows:
        aid = (row.get("anon_id") or "").strip()
        if not aid:
            continue
        ht = to_float(row.get("Total Score"))
        if ht is None:
            continue
        ai = ai_by_student.get(aid)
        if ai is None:
            continue
        h_list.append(float(ht))
        a_list.append(float(ai.total_score))
    return h_list, a_list, None


def _markdown_table(headers: list[str], body: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in body:
        cells = ["" if c is None else str(c) for c in row]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _truncate_text(s: str, limit: int = 1200) -> str:
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n\n… ({len(s) - limit} more chars)"


def _collect_string_leaves(obj: Any, prefix: str = "") -> list[tuple[str, str]]:
    """All (json_path, string_value) leaves with non-empty strings."""
    out: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                out.extend(_collect_string_leaves(v, key))
            elif isinstance(v, str) and v.strip():
                out.append((key, v))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:500]):
            out.extend(_collect_string_leaves(v, f"{prefix}[{i}]"))
    return out


def _parsed_excerpt(
    parsed_path: Path, limit: int = 2000, *, path_contains: str | None = None
) -> str:
    if not parsed_path.is_file():
        return f"*(missing {parsed_path.name})*"
    try:
        data = json.loads(parsed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"*(invalid JSON: {e})*"
    leaves = _collect_string_leaves(data)
    needle = (path_contains or "").strip()
    if needle:

        def _sort_key(kv: tuple[str, str]) -> tuple[int, str]:
            return (0 if needle in kv[0] else 1, kv[0])

        leaves = sorted(leaves, key=_sort_key)
    chunks = [f"**{k}**:\n{_truncate_text(v, 400)}" for k, v in leaves]
    text = "\n\n".join(chunks)
    return _truncate_text(text, limit)


def _rubric_snippet(config_path: Path, qid: str) -> str:
    if not config_path.is_file():
        return f"*(missing config: {config_path})*"
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        cfg = ensure_app_config(raw)
    except Exception as e:
        return f"*(config error: {e})*"
    entry = cfg.rubrics.get(qid)
    if entry is None:
        return f"*(no rubric for qid {qid} in config)*"
    lines = [f"Points: {entry.points}", ""]
    for it in entry.items:
        lines.append(f"- ({it.deduction} pts) {it.description}")
    return "\n".join(lines)


def _task1_section(project_root: Path) -> str:
    lines: list[str] = []
    lines.append(
        "Historical S23/S24 cohort trees are **not** in this repository. "
        "When raw Gradescope exports exist, mirror the S25/S26 layout under "
        "`experiment_data/<cohort>/<lab>/human_grades.csv` and stage notebooks under "
        "`output/<assignment_name>/` using `scripts/build_experiment_layout.py` and "
        "`prepare_assignment.py`, then run grading, for example:\n"
    )
    lines.append(
        "```bash\n"
        "python scripts/run_experiment_grading.py --labs S23_LabTest_2 --model gpt-4.1\n"
        "```\n"
    )
    lines.append(
        "The canonical `human_grades.csv` does **not** include per-submission grader "
        "(CA) identity; stratifying by grader requires raw export columns or manual joins.\n"
    )

    raw_dir = project_root / "experiment_data"
    raw_csvs = sorted(raw_dir.glob("**/_raw/**/*.csv")) if raw_dir.is_dir() else []
    if not raw_csvs:
        lines.append(
            "\nNo `experiment_data/**/_raw/**/*.csv` files found — skipped grader-column scan.\n"
        )
    else:
        lines.append("\n### Raw CSV grader-column scan (`_raw/`)\n")
        table_rows: list[list[str]] = []
        for p in raw_csvs[:50]:
            try:
                with p.open(encoding="utf-8", newline="") as f:
                    r = csv.reader(f)
                    header = next(r, [])
                cols = detect_grader_like_columns(header)
                table_rows.append(
                    [
                        str(p.relative_to(project_root)),
                        ", ".join(cols) if cols else "— (no grader-like header)",
                    ]
                )
            except OSError as e:
                table_rows.append(
                    [str(p.relative_to(project_root)), f"read error: {e}"]
                )
        if len(raw_csvs) > 50:
            lines.append(f"*(Showing first 50 of {len(raw_csvs)} raw CSV paths.)*\n")
        lines.append(
            _markdown_table(
                ["Raw CSV", "Grader-like columns"],
                table_rows,
            )
        )
        lines.append("")
    return "\n".join(lines)


def _task5_section(
    project_root: Path,
    *,
    model_tag: str | None,
    use_live: bool,
) -> str:
    lab = TASK5_LAB
    gp = graded_results_path(
        lab, project_root=project_root, model_tag=model_tag, use_live=use_live
    )
    cfg_path = project_root / "output" / lab / "config.yaml"
    parsed_dir = project_root / "output" / lab / "parsed"
    lines: list[str] = []
    if not gp.is_file():
        lines.append(
            f"*(No graded results at `{gp}` — cannot show scores or feedback.)*\n"
        )
        ai_by: dict[str, Any] = {}
    else:
        ai_by = load_ai_by_student(gp)

    human_path = human_csv_path(lab, project_root=project_root)
    human_cell: dict[str, dict[str, float | None]] = {}
    if human_path.is_file():
        with human_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
        qcols = qid_columns_from_headers(fieldnames)
        col_121 = qcols.get(TASK5_QID)
        for row in rows:
            aid = (row.get("anon_id") or "").strip()
            if aid not in TASK5_STUDENTS:
                continue
            hq = to_float(row.get(col_121)) if col_121 else None
            human_cell[aid] = {"q": hq, "total": to_float(row.get("Total Score"))}

    for stem in TASK5_STUDENTS:
        lines.append(f"#### Student `{stem}`\n")
        hinfo = human_cell.get(stem, {})
        lines.append(
            f"- Human **Total Score**: {hinfo.get('total')}\n"
            f"- Human **{TASK5_QID}** (CSV): {hinfo.get('q')}\n"
        )
        ai = ai_by.get(stem)
        if ai is None:
            lines.append("- AI: *(no row in graded_results.json)*\n")
            continue
        qd = ai.questions.get(TASK5_QID)
        if qd is None:
            lines.append(
                f"- AI total: {ai.total_score} / {ai.total_max}\n"
                f"- AI **{TASK5_QID}**: *(missing in JSON)*\n"
            )
        else:
            lines.append(
                f"- AI total: {ai.total_score} / {ai.total_max}\n"
                f"- AI **{TASK5_QID}**: {qd.score} / {qd.max}\n"
            )
            lines.append(
                "\n**Model feedback** (`Question.feedback`; no separate CoT log):\n\n"
            )
            lines.append("```\n" + (qd.feedback or "(empty)") + "\n```\n")
        pj = parsed_dir / f"{stem}.json"
        lines.append("\n**Parsed notebook excerpt**\n\n")
        lines.append(_parsed_excerpt(pj, path_contains=TASK5_QID) + "\n")
        lines.append("\n**Rubric snippet** (`output/.../config.yaml`)\n\n")
        lines.append(_rubric_snippet(cfg_path, TASK5_QID) + "\n")

    return "\n".join(lines)


def build_markdown_report(
    *,
    project_root: Path,
    labs: list[str],
    model_tag: str | None,
    use_live: bool,
    over_frac: float,
    under_frac: float,
) -> str:
    parts: list[str] = []
    parts.append("# Human–AI discrepancy analysis\n")
    parts.append(
        "Per-question join: `anon_id` (CSV) == `student_name` (JSON). "
        "Question maxima for Tasks 2–3 are taken from **AI** `graded_results.json` "
        "for apples-to-apples denominators.\n"
    )
    parts.append(
        f"**Definition (Task 4)** MAE uses **per-student** absolute error on **lab total**: "
        f"|AI `total_score` − human `Total Score`| (same headline totals as "
        f"`scripts/compare_experiment_to_human.py`).\n"
    )

    parts.append("## Task 1 — Historical / CA / prerequisites\n\n")
    parts.append(_task1_section(project_root))

    all_task2: list[dict[str, Any]] = []
    all_task3: list[dict[str, Any]] = []
    task4_blocks: list[str] = []

    for lab in labs:
        gp = graded_results_path(
            lab, project_root=project_root, model_tag=model_tag, use_live=use_live
        )
        anons, qcols, matrix, err = _load_paired_question_matrix(
            lab, project_root=project_root, graded_path=gp
        )
        if err:
            task4_blocks.append(f"### `{lab}`\n\n*{err}*\n")
            continue

        t2 = _task2_rows(matrix, anons, qcols, over_frac)
        t3 = _task3_rows(matrix, anons, qcols, under_frac)
        for r in t2:
            r["lab"] = lab
        for r in t3:
            r["lab"] = lab
        all_task2.extend(t2)
        all_task3.extend(t3)

        h_tot, a_tot, _ = _paired_totals_for_lab(
            lab, project_root=project_root, graded_path=gp
        )
        tier_stats = _task4_tier_stats(h_tot, a_tot) if h_tot else []
        t4_md = _markdown_table(
            ["Tier", "n", "mean |Δ_total|", "median |Δ_total|"],
            [
                [
                    x["tier"],
                    x["n"],
                    (
                        ""
                        if x["mean_abs_delta_total"] is None
                        else f"{x['mean_abs_delta_total']:.4f}"
                    ),
                    (
                        ""
                        if x["median_abs_delta_total"] is None
                        else f"{x['median_abs_delta_total']:.4f}"
                    ),
                ]
                for x in tier_stats
            ],
        )
        task4_blocks.append(f"### `{lab}`\n\n{t4_md}\n")

    parts.append("\n## Task 2 — Human = 0, AI above fraction of AI max\n\n")
    parts.append(
        f"Rule: human question score == 0 and AI score > {over_frac:g} × AI max.\n\n"
    )
    parts.append(
        _markdown_table(
            ["lab", "anon_id", "qid", "human", "ai_score", "ai_max"],
            [
                [
                    r["lab"],
                    r["anon_id"],
                    r["qid"],
                    r["human"],
                    r["ai_score"],
                    r["ai_max"],
                ]
                for r in sorted(
                    all_task2, key=lambda x: (x["lab"], x["anon_id"], x["qid"])
                )
            ],
        )
        if all_task2
        else "_No matching rows._\n"
    )

    parts.append("\n## Task 3 — Human above fraction of max, AI = 0\n\n")
    parts.append(
        f"Rule: human > {under_frac:g} × (AI max for that question) and AI score == 0.\n\n"
    )
    parts.append(
        _markdown_table(
            ["lab", "anon_id", "qid", "human", "ai_score", "ai_max"],
            [
                [
                    r["lab"],
                    r["anon_id"],
                    r["qid"],
                    r["human"],
                    r["ai_score"],
                    r["ai_max"],
                ]
                for r in sorted(
                    all_task3, key=lambda x: (x["lab"], x["anon_id"], x["qid"])
                )
            ],
        )
        if all_task3
        else "_No matching rows._\n"
    )

    parts.append("\n## Task 4 — Total-score |Δ| by human-total quartile (per lab)\n\n")
    parts.extend(task4_blocks)

    parts.append(
        "\n## Task 5 — Case study: S25 LabTest 7, question 1.21 (students 001 & 003)\n\n"
    )
    parts.append(
        "Stored **reasoning** is limited to **`Question.feedback`** in JSON; "
        "there is no separate chain-of-thought log unless you add logging.\n\n"
    )
    parts.append(_task5_section(project_root, model_tag=model_tag, use_live=use_live))

    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write Markdown human–AI discrepancy report.",
    )
    parser.add_argument(
        "--model-tag",
        type=str,
        default="gpt-4.1",
        help="experiment_runs/<tag>/graded_results.json (ignored if --use-live-graded)",
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
        help="Comma-separated assignment_name values.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiment_analysis/human_ai_discrepancies.md"),
        help="Markdown output path",
    )
    parser.add_argument(
        "--over-human-zero-ai-frac",
        type=float,
        default=0.7,
        dest="over_frac",
        help="Task 2: AI score must exceed this fraction of AI max (human=0).",
    )
    parser.add_argument(
        "--under-human-frac",
        type=float,
        default=0.7,
        dest="under_frac",
        help="Task 3: human score must exceed this fraction of AI max (AI=0).",
    )
    args = parser.parse_args()

    if args.use_live_graded:
        tag: str | None = None
    else:
        tag = args.model_tag

    labs = [x.strip() for x in args.labs.split(",") if x.strip()]
    md = build_markdown_report(
        project_root=PROJECT_ROOT,
        labs=labs,
        model_tag=tag,
        use_live=args.use_live_graded,
        over_frac=args.over_frac,
        under_frac=args.under_frac,
    )
    out = args.output
    if not out.is_absolute():
        out = PROJECT_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
