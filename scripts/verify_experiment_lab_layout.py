#!/usr/bin/env python3
"""Verify canonical experiment_data lab: parse qids vs human_grades columns; anon_id vs submissions."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_models import ensure_app_config
from parse_notebook import get_all_question_ids, parse_notebook


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lab-dir", type=Path, required=True)
    args = ap.parse_args()
    lab = args.lab_dir.resolve()
    cfg_path = lab / "config.yaml"
    sol = lab / "solution.ipynb"
    hg = lab / "human_grades.csv"
    sub = lab / "submissions"

    if not cfg_path.is_file():
        print(f"Missing {cfg_path}", file=sys.stderr)
        return 1
    if not sol.is_file():
        print(f"Missing {sol}", file=sys.stderr)
        return 1
    if not hg.is_file():
        print(f"Missing {hg}", file=sys.stderr)
        return 1
    if not sub.is_dir():
        print(f"Missing {sub}", file=sys.stderr)
        return 1

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    raw["solution_notebook"] = str(sol.resolve())
    cfg = ensure_app_config(raw)
    parsed = parse_notebook(sol, cfg)
    parse_ids = set(get_all_question_ids(parsed))

    with open(hg, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        meta = {"anon_id", "Total Score", "Max Points"}
        hg_fields = list(reader.fieldnames or [])
        qcols = [c for c in hg_fields if c not in meta]

    if not set(qcols).issubset(parse_ids):
        print(
            "human_grades qids not subset of parse:",
            sorted(set(qcols) - parse_ids)[:30],
            file=sys.stderr,
        )
        return 1
    if len(qcols) < len(parse_ids):
        print(
            f"NOTE: parsed {len(parse_ids)} questions; human_grades has {len(qcols)} "
            f"(grade_only may omit tail — OK if intentional)"
        )

    stems = {p.stem for p in sub.glob("*.ipynb")}
    with open(hg, newline="", encoding="utf-8") as f:
        anons = {str(r.get("anon_id", "")).strip() for r in csv.DictReader(f)}
    anons.discard("")

    if stems != anons:
        print(
            "submissions stems != human_grades anon_id:",
            "only_sub",
            sorted(stems - anons),
            "only_csv",
            sorted(anons - stems),
            file=sys.stderr,
        )
        return 1

    print(
        f"OK {lab.name}: {len(parse_ids)} questions, {len(stems)} submissions, "
        f"total_points={sum(q.get('points', 0) for sec in parsed['sections'].values() for q in sec['questions'].values())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
