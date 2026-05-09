#!/usr/bin/env python3
"""Rewrite Gradescope ``grades.csv`` headers so the **notebook** QID matches the parser.

Many S24 exports use::

    <gradescope_qid>: <notebook_qid> (<pts> pts)

``canonicalize_experiment_lab.py`` joins human scores using ``notebook_qid`` (after the
colon). If you renumbered ``# <font>N`` sections in notebooks, the parser’s QIDs move
but the export still has **old** notebook labels — fix the export (or a copy) with this
script, then re-run canonicalize.

Mapping file: JSON object **old notebook qid → new notebook qid** (only list keys that
change), e.g.::

    {"5.1": "6.1", "6.1": "7.1", "7.1": "8.1"}

Use ``--show-headers`` to print ``gradescope | notebook | pts`` for each question column
so you can build the map.

Examples::

    python scripts/remap_gradescope_notebook_qids.py \\
        experiment_data/S24/LabTest_5/_raw/grades.csv --show-headers

    python scripts/remap_gradescope_notebook_qids.py \\
        experiment_data/S24/LabTest_5/_raw/grades.csv \\
        --mapping my_map.json \\
        --output experiment_data/S24/LabTest_5/_raw/grades_remapped.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_models import normalize_qid

# Same shape as build_experiment_layout._DUAL_GS_NB_HEADER (question columns only).
_DUAL_GS_NB_HEADER = re.compile(
    r"^\s*(?P<gs>\d+\.\d+)\s*:\s*(?P<nb>\d+\.\d+)\s*\(\s*(?P<pts>[\d.]+)\s*pts?\s*\)",
    re.IGNORECASE,
)

_META_COLS = frozenset(
    {
        "anon_id",
        "Total Score",
        "Max Points",
        "Status",
        "Submission Time",
        "Lateness (H:M:S)",
        "View Count",
        "Submission Count",
        "Submission ID",
        "First Name",
        "Last Name",
        "SID",
        "Email",
        "Sections",
    }
)


def _is_question_column(header: str) -> bool:
    if header in _META_COLS:
        return False
    if "Autograder" in header:
        return False
    return True


def remap_gradescope_header(header: str, mapping: dict[str, str]) -> tuple[str, bool]:
    """If header matches ``gs: nb (pts)``, replace ``nb`` when ``mapping[nb]`` is set.

    Returns ``(new_header, changed)``. Non-dual headers and unmapped notebook ids unchanged.
    """
    if not _is_question_column(header):
        return header, False
    lead = len(header) - len(header.lstrip())
    stripped = header.strip()
    m = _DUAL_GS_NB_HEADER.match(stripped)
    if not m:
        return header, False
    nb = normalize_qid(m.group("nb"))
    new_nb = mapping.get(nb)
    if new_nb is None:
        return header, False
    new_nb = normalize_qid(new_nb)
    if new_nb == nb:
        return header, False
    nb_start = lead + m.start("nb")
    nb_end = lead + m.end("nb")
    return header[:nb_start] + new_nb + header[nb_end:], True


def _load_mapping(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Mapping must be a JSON object, got {type(raw)}")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError(f"Mapping keys/values must be strings: {k!r} -> {v!r}")
        out[normalize_qid(k.strip())] = normalize_qid(v.strip())
    return out


def show_headers(headers: list[str]) -> None:
    print("gradescope_qid\tnotebook_qid\tpts\theader")
    for h in headers:
        if not _is_question_column(h):
            continue
        m = _DUAL_GS_NB_HEADER.match(h.strip())
        if not m:
            print(f"(non-dual)\t\t\t{h!r}")
            continue
        print(
            f"{m.group('gs')}\t{m.group('nb')}\t{m.group('pts')}\t{h!r}",
        )


def remap_csv(
    path: Path,
    mapping: dict[str, str],
    *,
    out_path: Path | None,
    in_place: bool,
) -> list[tuple[str, str]]:
    """Return list of (old_header, new_header) for headers that changed."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        old_headers = list(reader.fieldnames or [])
        rows = list(reader)

    new_headers: list[str] = []
    changes: list[tuple[str, str]] = []
    for h in old_headers:
        nh, ch = remap_gradescope_header(h, mapping)
        if ch:
            changes.append((h, nh))
        new_headers.append(nh)

    if len(set(new_headers)) != len(new_headers):
        from collections import Counter

        dupes = [k for k, v in Counter(new_headers).items() if v > 1]
        raise ValueError(
            f"Duplicate column names after remap (first dupes): {dupes[:10]}"
        )

    target = out_path
    if in_place:
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        target = path

    buf = io.StringIO()
    w = csv.DictWriter(
        buf,
        fieldnames=new_headers,
        lineterminator="\n",
        extrasaction="ignore",
    )
    w.writeheader()
    key_map = dict(zip(old_headers, new_headers, strict=True))
    for row in rows:
        w.writerow({key_map[k]: row.get(k, "") for k in old_headers})

    out_text = buf.getvalue()
    if target is None:
        sys.stdout.write(out_text)
    else:
        target.write_text(out_text, encoding="utf-8")

    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("grades_csv", type=Path, help="Gradescope grades export")
    ap.add_argument(
        "--mapping",
        type=Path,
        default=None,
        help="JSON object: old_notebook_qid -> new_notebook_qid",
    )
    ap.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Write remapped CSV here (default: stdout if not --in-place)",
    )
    ap.add_argument(
        "--in-place",
        action="store_true",
        help="Overwrite input; keeps a .bak copy next to the file",
    )
    ap.add_argument(
        "--show-headers",
        action="store_true",
        help="Print gradescope | notebook | pts and exit",
    )
    args = ap.parse_args()

    path = args.grades_csv.resolve()
    if not path.is_file():
        print(f"Not found: {path}", file=sys.stderr)
        return 2

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])

    if args.show_headers:
        show_headers(headers)
        return 0

    if args.mapping is None:
        print("Provide --mapping FILE.json or use --show-headers", file=sys.stderr)
        return 2

    mapping = _load_mapping(args.mapping.resolve())
    if args.in_place and args.output is not None:
        print("Use only one of --in-place or --output", file=sys.stderr)
        return 2
    if not args.in_place and args.output is None:
        print("Provide --output PATH or --in-place", file=sys.stderr)
        return 2

    changes = remap_csv(
        path,
        mapping,
        out_path=args.output.resolve() if args.output else None,
        in_place=args.in_place,
    )
    for o, n in changes:
        print(f"{o!r} -> {n!r}")
    print(f"Updated {len(changes)} column(s).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
