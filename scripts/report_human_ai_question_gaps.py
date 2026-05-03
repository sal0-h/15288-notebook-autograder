#!/usr/bin/env python3
"""List largest per-question score gaps (AI vs human) per lab, not averaged.

Joins human_grades.csv to graded_results.json like compare_experiment_to_human.py.
For each lab, prints top N pairs where AI > human and top N where AI < human,
sorted by |AI − human| (largest discrepancy first).

Example:
    python scripts/report_human_ai_question_gaps.py --model-tag gpt-4.1 \\
        --out research/paper/gpt41_top_question_discrepancies.md
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_models import normalize_qid
from results_store import load_results

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


def _human_csv_path(assignment_name: str) -> Path:
    cohort, lab = HUMAN_CSV_REL[assignment_name]
    return PROJECT_ROOT / "experiment_data" / cohort / lab / "human_grades.csv"


def _header_to_qid(header: str) -> str | None:
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


def _to_float(x: object) -> float | None:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def collect_gaps(
    assignment: str, model_tag: str
) -> tuple[list[tuple[float, str, str, float, float, float]], list]:
    hp = _human_csv_path(assignment)
    gp = (
        PROJECT_ROOT
        / "output"
        / assignment
        / "experiment_runs"
        / model_tag
        / "graded_results.json"
    )
    if not hp.is_file():
        return [], []
    if not gp.is_file():
        return [], []

    with hp.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        human_rows = list(reader)
        fields = reader.fieldnames or []

    qid_cols: dict[str, str] = {}
    for h in fields:
        q = _header_to_qid(h)
        if q:
            qid_cols[q] = h

    ai_by = {r.student_name.strip(): r for r in load_results(gp) if r.student_name}

    higher: list[tuple[float, str, str, float, float, float]] = []
    lower: list[tuple[float, str, str, float, float, float]] = []
    for row in human_rows:
        sid = (row.get("anon_id") or "").strip()
        if not sid or sid not in ai_by:
            continue
        ai = ai_by[sid]
        for qid, col in qid_cols.items():
            hq = _to_float(row.get(col))
            if hq is None:
                continue
            qd = ai.questions.get(qid)
            if qd is None:
                continue
            aq = float(qd.score)
            amax = float(qd.max)
            diff = aq - hq
            rec = (diff, sid, qid, hq, aq, amax)
            if diff > 0:
                higher.append(rec)
            elif diff < 0:
                lower.append(rec)

    higher.sort(key=lambda t: -t[0])
    lower.sort(key=lambda t: t[0])
    return higher, lower


def _fmt_line(i: int, t: tuple[float, str, str, float, float, float]) -> str:
    diff, sid, qid, hq, aq, amax = t
    return (
        f"{i}. student {sid}, q {qid}, human={hq:g}, AI={aq:g} (max {amax:g}), "
        f"AI−human={diff:+.4g}\n"
    )


def run_report(
    *,
    model_tag: str,
    labs: list[str],
    top_n: int,
    out: Path | None,
) -> str:
    chunks: list[str] = []
    for lab in labs:
        hi, lo = collect_gaps(lab, model_tag)
        chunks.append(f"\n## {lab} ({model_tag})\n")
        chunks.append(
            "### AI score higher than human (top {}, largest gap first)\n".format(top_n)
        )
        if not hi:
            chunks.append("(none)\n")
        else:
            for i, t in enumerate(hi[:top_n], 1):
                chunks.append(_fmt_line(i, t))
        chunks.append(
            "\n### AI score lower than human (top {}, largest gap first)\n".format(
                top_n
            )
        )
        if not lo:
            chunks.append("(none)\n")
        else:
            for i, t in enumerate(lo[:top_n], 1):
                chunks.append(_fmt_line(i, t))
    text = "".join(chunks)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    return text


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--model-tag", default="gpt-4.1", help="experiment_runs/<tag>/ folder name"
    )
    p.add_argument(
        "--labs",
        default=",".join(HUMAN_CSV_REL.keys()),
        help="Comma-separated assignment_name values",
    )
    p.add_argument(
        "--top", type=int, default=10, help="How many pairs per direction per lab"
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write markdown here (default: print to stdout only)",
    )
    args = p.parse_args()
    labs = [x.strip() for x in args.labs.split(",") if x.strip()]
    text = run_report(model_tag=args.model_tag, labs=labs, top_n=args.top, out=args.out)
    if args.out is None:
        sys.stdout.write(text)
    else:
        print(f"Wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
