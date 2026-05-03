#!/usr/bin/env python3
"""Structural audit of staged experiment rubrics under ``output/<assignment>/config.yaml``.

Reads YAML from disk (works when ``output/`` is gitignored and IDE file tools cannot).
Checks: ``question_groups`` coverage vs ``rubrics`` keys, sum of ``items[].deduction`` vs
``points``, optional coarse single-item heuristic (one item, deduction ≥ threshold).

Example::

    python scripts/audit_experiment_output_rubrics.py
    python scripts/audit_experiment_output_rubrics.py --lab S25_LabTest_4
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from config_models import normalize_qid

DEFAULT_GLOB = "S*_LabTest_*/config.yaml"


def audit_config_dict(
    cfg: dict[str, Any], *, coarse_min_deduction: float
) -> dict[str, Any]:
    """Return audit result dict (no IO)."""
    rubrics = cfg.get("rubrics") or {}
    groups = (cfg.get("grading") or {}).get("question_groups") or []
    g_flat: list[str] = []
    for g in groups:
        for q in g:
            g_flat.append(normalize_qid(str(q)))
    g_set = set(g_flat)
    r_set = {normalize_qid(str(k)) for k in rubrics}

    missing = sorted(g_set - r_set)
    orphan = sorted(r_set - g_set)
    mismatch: list[tuple[str, float, float]] = []
    empty_pts: list[tuple[str, float]] = []
    coarse: list[str] = []

    for qid_raw, entry in rubrics.items():
        q = normalize_qid(str(qid_raw))
        pts = float(entry.get("points") or 0)
        items = entry.get("items") or []
        if not items and pts > 0:
            empty_pts.append((q, pts))
            continue
        if items:
            s = sum(float(i.get("deduction") or 0) for i in items)
            if abs(s - pts) > 0.01:
                mismatch.append((q, pts, s))
            if (
                len(items) == 1
                and float(items[0].get("deduction") or 0) >= coarse_min_deduction
            ):
                coarse.append(q)

    structural_ok = not missing and not mismatch and not empty_pts
    return {
        "assignment_name": cfg.get("assignment_name", ""),
        "n_rubrics": len(rubrics),
        "n_group_qids": len(g_set),
        "missing_rubric": missing,
        "orphan_rubric": orphan,
        "mismatch": mismatch,
        "empty_items_pts_positive": empty_pts,
        "coarse_single_ge_threshold": sorted(coarse),
        "structural_ok": structural_ok,
    }


def _print_report(path: Path, r: dict[str, Any], *, coarse_min: float) -> None:
    name = r["assignment_name"] or path.parent.name
    st = "PASS" if r["structural_ok"] else "FAIL"
    print(f"\n{'=' * 72}")
    print(f"{path}")
    print(f"  assignment_name={name!r}  structural={st}")
    print(
        f"  rubrics={r['n_rubrics']}  distinct_group_qids={r['n_group_qids']}  "
        f"orphan_rubric_keys={len(r['orphan_rubric'])}"
    )
    if r["missing_rubric"]:
        print(
            f"  IN_GROUPS_NO_RUBRIC ({len(r['missing_rubric'])}): {r['missing_rubric']}"
        )
    if r["mismatch"]:
        print("  sum(deductions)!=points:")
        for q, pts, s in r["mismatch"]:
            print(f"    {q}  points={pts}  sum={s:.4f}")
    if r["empty_items_pts_positive"]:
        print("  EMPTY items but points>0:", r["empty_items_pts_positive"])
    if r["orphan_rubric"]:
        print(
            f"  rubric_not_in_any_group ({len(r['orphan_rubric'])}): {r['orphan_rubric']}"
        )
    if r["coarse_single_ge_threshold"]:
        print(
            f"  coarse (single item, ded>={coarse_min:g}): "
            f"{r['coarse_single_ge_threshold']}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "output",
        help="Directory containing assignment folders (default: repo output/)",
    )
    ap.add_argument(
        "--lab",
        action="append",
        dest="labs",
        metavar="NAME",
        help="Assignment folder name (repeatable), e.g. S25_LabTest_4. Default: all S*_LabTest_*",
    )
    ap.add_argument(
        "--coarse-min-deduction",
        type=float,
        default=5.0,
        help="Flag rubrics with exactly one item whose deduction is >= this (default: 5)",
    )
    args = ap.parse_args()

    root: Path = args.output_root
    if args.labs:
        paths = sorted(root / lab / "config.yaml" for lab in args.labs)
    else:
        paths = sorted(root.glob(DEFAULT_GLOB))

    missing_files = [p for p in paths if not p.is_file()]
    paths = [p for p in paths if p.is_file()]

    if missing_files:
        print("Missing config.yaml:", *[str(p) for p in missing_files], sep="\n  ")

    any_fail = bool(missing_files)
    for path in paths:
        cfg = yaml.safe_load(path.read_text())
        r = audit_config_dict(cfg, coarse_min_deduction=args.coarse_min_deduction)
        _print_report(path, r, coarse_min=args.coarse_min_deduction)
        if not r["structural_ok"]:
            any_fail = True

    if not paths and not missing_files:
        print(f"No configs matched under {root} ({DEFAULT_GLOB}).")
        return 1

    print(f"\n{'=' * 72}")
    print("SUMMARY: structural", "FAIL" if any_fail else "all PASS")
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
