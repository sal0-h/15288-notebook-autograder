#!/usr/bin/env python3
"""Snapshot graded_results.json and experiment_runs/ under output/.

Writes to ``grading_backups/<timestamp>/`` by default (gitignored except README).
See ``grading_backups/README.md``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def backup_grading_results(
    project_root: Path,
    dest: Path,
    *,
    include_experiment_analysis: bool,
) -> list[str]:
    """Copy grading artifacts. Returns log lines."""
    dest.mkdir(parents=True, exist_ok=True)
    manifest: list[str] = []
    out = project_root / "output"
    if not out.is_dir():
        manifest.append("no output/ directory — nothing copied")
        return manifest

    for assign_dir in sorted(out.iterdir(), key=lambda p: p.name):
        if not assign_dir.is_dir():
            continue
        graded = assign_dir / "graded_results.json"
        er = assign_dir / "experiment_runs"
        if not graded.is_file() and not er.is_dir():
            continue
        dst = dest / "output" / assign_dir.name
        dst.mkdir(parents=True, exist_ok=True)
        if graded.is_file():
            shutil.copy2(graded, dst / "graded_results.json")
            manifest.append(f"OK {assign_dir.name}/graded_results.json")
        if er.is_dir():
            target = dst / "experiment_runs"
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(er, target)
            manifest.append(f"OK {assign_dir.name}/experiment_runs/")

    if include_experiment_analysis:
        ea = project_root / "experiment_analysis"
        if ea.is_dir():
            target = dest / "experiment_analysis"
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(ea, target)
            manifest.append("OK experiment_analysis/")
        else:
            manifest.append("skip experiment_analysis/ (missing)")

    (dest / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Backup root (default: grading_backups/<UTC timestamp>)",
    )
    parser.add_argument(
        "--include-experiment-analysis",
        action="store_true",
        help="Also copy experiment_analysis/ when present",
    )
    args = parser.parse_args()
    if args.dest is not None:
        dest = args.dest
        if not dest.is_absolute():
            dest = project_root / dest
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = project_root / "grading_backups" / stamp

    lines = backup_grading_results(
        project_root,
        dest,
        include_experiment_analysis=args.include_experiment_analysis,
    )
    print(f"Backup root: {dest}", file=sys.stderr)
    for ln in lines:
        print(ln, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
