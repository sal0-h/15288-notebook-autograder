#!/usr/bin/env python3
"""TEMP: Renumber CMU-style ``# <font…>N`` section headers so IDs strictly increase.

When two blocks reuse the same section digit (e.g. two ``# <font…>2 …`` lines),
``parse_notebook`` assigns synthetic sections like ``2002`` for the repeat. This
script walks notebooks in **document order** and replaces each header's section
number ``N`` with ``max(N, prev+1)``, so duplicates become the next free integer
and all following headers shift as needed.

Typical Lab 5 fix: ``…>2 Binary…``, ``…>2 Match…``, ``…>3 Ensemble…`` →
``2``, ``3``, ``4``.

Usage (then delete this file when done)::

    uv run python scripts/temp_renumber_section_headers.py \\
        --lab-dir experiment_data/S24/LabTest_5 --write

    # Preview only
    uv run python scripts/temp_renumber_section_headers.py \\
        --lab-dir experiment_data/S24/LabTest_5

By default processes ``solution.ipynb``, ``_raw/*_sol.ipynb``, and
``submissions/*.ipynb`` under ``--lab-dir``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Match a markdown line that opens a section (same idea as S24 section_regex + line start).
_SECTION_HEADER = re.compile(
    r"(?m)^(\s*#\s*<font[^>]*>\s*)(\d+)(\b)",
    re.IGNORECASE,
)


def _collect_matches(source: str) -> list[re.Match[str]]:
    return list(_SECTION_HEADER.finditer(source))


def _strict_increasing(nums: list[int]) -> list[int]:
    out: list[int] = []
    prev: int | None = None
    for v in nums:
        if prev is None:
            w = v
        else:
            w = max(v, prev + 1)
        out.append(w)
        prev = w
    return out


def _apply_replacements(
    source: str, matches: list[re.Match[str]], new_nums: list[int]
) -> str:
    assert len(matches) == len(new_nums)
    pieces: list[str] = []
    pos = 0
    for m, new_n in zip(matches, new_nums):
        old_n = int(m.group(2))
        if old_n == new_n:
            pieces.append(source[pos : m.end()])
        else:
            pieces.append(source[pos : m.start(2)])
            pieces.append(str(new_n))
            pieces.append(source[m.end(2) : m.end()])
        pos = m.end()
    pieces.append(source[pos:])
    return "".join(pieces)


def renumber_markdown_sources(nb: dict) -> tuple[int, list[tuple[int, int, int]]]:
    """Return (cells_changed, list of (cell_index, old_num, new_num) for each edited header)."""
    report: list[tuple[int, int, int]] = []
    changed = 0
    cells = nb.get("cells", [])

    ordered: list[tuple[int, re.Match[str]]] = []
    for ci, cell in enumerate(cells):
        if cell.get("cell_type") != "markdown":
            continue
        src = "".join(cell.get("source", []))
        for m in _collect_matches(src):
            ordered.append((ci, m))

    if not ordered:
        return 0, report

    old_nums = [int(m.group(2)) for _, m in ordered]
    new_nums = _strict_increasing(old_nums)

    by_cell: dict[int, list[tuple[re.Match[str], int]]] = {}
    for (ci, m), new_n in zip(ordered, new_nums):
        old_n = int(m.group(2))
        if old_n != new_n:
            report.append((ci, old_n, new_n))
        by_cell.setdefault(ci, []).append((m, new_n))

    for ci, pairs in by_cell.items():
        cell = cells[ci]
        src = "".join(cell.get("source", []))
        matches = [p[0] for p in pairs]
        nums = [p[1] for p in pairs]
        new_src = _apply_replacements(src, matches, nums)
        if new_src != src:
            changed += 1
            cell["source"] = new_src.splitlines(keepends=True)
            if cell["source"] and not cell["source"][-1].endswith("\n"):
                # normalize trailing newline like Jupyter
                cell["source"][-1] += "\n"

    return changed, report


def _iter_default_notebooks(lab_dir: Path) -> list[Path]:
    out: list[Path] = []
    sol = lab_dir / "solution.ipynb"
    if sol.is_file():
        out.append(sol)
    raw = lab_dir / "_raw"
    if raw.is_dir():
        out.extend(sorted(raw.glob("*_sol.ipynb")))
    sub = lab_dir / "submissions"
    if sub.is_dir():
        out.extend(sorted(sub.glob("*.ipynb")))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--lab-dir",
        type=Path,
        default=None,
        help="Lab folder; renumbers solution, _raw/*_sol.ipynb, submissions/*.ipynb",
    )
    ap.add_argument(
        "notebooks",
        nargs="*",
        type=Path,
        help="Explicit .ipynb paths (if empty, use --lab-dir default set)",
    )
    ap.add_argument(
        "--write",
        action="store_true",
        help="Write notebooks; default is dry-run (print summary only)",
    )
    args = ap.parse_args()

    paths: list[Path] = [p.resolve() for p in args.notebooks]
    if args.lab_dir:
        paths.extend(_iter_default_notebooks(args.lab_dir.resolve()))
    # de-dupe preserving order
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in paths:
        if p not in seen and p.is_file():
            seen.add(p)
            uniq.append(p)

    if not uniq:
        print("No notebooks found.", file=sys.stderr)
        return 2

    total_files = 0
    any_edits = False
    for path in uniq:
        text = path.read_text(encoding="utf-8")
        nb = json.loads(text)
        n_changed, report = renumber_markdown_sources(nb)
        if report or n_changed:
            any_edits = True
            print(f"{path}:")
            for ci, old_n, new_n in report:
                print(f"  cell {ci}: section {old_n} -> {new_n}")
        else:
            print(f"{path}: (no changes)")
        if args.write and (report or n_changed):
            path.write_text(
                json.dumps(nb, indent=1, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            total_files += 1

    if not args.write and any_edits:
        print("\nDry-run only; pass --write to save.", file=sys.stderr)

    if args.write:
        print(f"Wrote {total_files} notebook(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
