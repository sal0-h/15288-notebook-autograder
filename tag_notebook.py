#!/usr/bin/env python3
"""Inject question-type tags into Jupyter notebook cell metadata.

Usage:
    python tag_notebook.py --notebook path/to/notebook.ipynb --tags tags.json
    python tag_notebook.py --notebook path/to/notebook.ipynb --tags tags.json --config output/HW2/config.yaml

tags.json example:
    {"1.1": "code", "1.2": "plot", "1.3": "analysis", "2.6": "open-ended"}

Valid types: code, plot, analysis, open-ended, exact, mixed
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Same set as parse_notebook.py
VALID_QUESTION_TYPES = {"code", "plot", "analysis", "open-ended", "exact", "mixed"}

# Default question regex (matches Q1.1 [4 PTS] etc.)
DEFAULT_QUESTION_REGEX = r"(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]"


def tag_notebook(
    nb_path: Path,
    tags: dict[str, str],
    question_regex: str = DEFAULT_QUESTION_REGEX,
    *,
    dry_run: bool = False,
) -> dict[str, str]:
    """Inject type: tags into notebook cells. Returns {qid: type} for cells that were tagged."""
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    cells = nb.get("cells", [])
    question_re = re.compile(question_regex, re.MULTILINE)
    tagged: dict[str, str] = {}

    for cell in cells:
        if cell.get("cell_type") != "markdown":
            continue
        text = "".join(cell.get("source", []))
        m = question_re.search(text)
        if not m:
            continue

        # Extract QID using same logic as parse_notebook
        if m.lastindex >= 4:
            sec_id, qnum = m.group(2), m.group(3)
        else:
            sec_id, qnum = m.group(1), m.group(2)
        qid = f"{sec_id}.{qnum}"

        if qid not in tags:
            continue

        qtype = tags[qid].strip().lower()
        if qtype not in VALID_QUESTION_TYPES:
            print(
                f"Warning: unknown type '{tags[qid]}' for {qid}, skipping",
                file=sys.stderr,
            )
            continue

        # Ensure metadata and tags exist (handle tags=None from malformed notebooks)
        meta = cell.setdefault("metadata", {})
        cell_tags = meta.get("tags")
        if not isinstance(cell_tags, list):
            cell_tags = []
            meta["tags"] = cell_tags

        # Remove any existing type: tag
        cell_tags[:] = [
            t for t in cell_tags if not (isinstance(t, str) and t.startswith("type:"))
        ]

        # Add the new type tag
        cell_tags.append(f"type:{qtype}")
        tagged[qid] = qtype

    if not dry_run:
        nb_path.write_text(json.dumps(nb, indent=1) + "\n", encoding="utf-8")

    return tagged


def main():
    parser = argparse.ArgumentParser(
        description="Inject question-type tags into Jupyter notebook cell metadata",
    )
    parser.add_argument(
        "--notebook", type=Path, required=True, help="Path to .ipynb file"
    )
    parser.add_argument(
        "--tags", type=Path, required=True, help="Path to JSON mapping {qid: type}"
    )
    parser.add_argument(
        "--regex",
        default=DEFAULT_QUESTION_REGEX,
        help="Question regex (default: standard Q pattern)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Optional: load question_regex from assignment config",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be tagged without writing",
    )
    args = parser.parse_args()

    if not args.notebook.exists():
        print(f"Error: notebook not found: {args.notebook}", file=sys.stderr)
        return 1
    if not args.tags.exists():
        print(f"Error: tags file not found: {args.tags}", file=sys.stderr)
        return 1

    tags = json.loads(args.tags.read_text(encoding="utf-8"))
    if not isinstance(tags, dict):
        print("Error: tags file must be a JSON object {qid: type}", file=sys.stderr)
        return 1

    regex = args.regex
    if args.config:
        from config_models import load_app_config

        cfg = load_app_config(args.config)
        regex = cfg.parsing.question_regex

    tagged = tag_notebook(args.notebook, tags, regex, dry_run=args.dry_run)

    if args.dry_run:
        print("Dry run — no changes written.")

    for qid, qtype in sorted(tagged.items()):
        print(f"  {'Would tag' if args.dry_run else 'Tagged'} {qid} → {qtype}")

    unmatched = set(tags.keys()) - set(tagged.keys())
    if unmatched:
        print(
            f"\nWarning: {len(unmatched)} QIDs not found in notebook: {sorted(unmatched)}",
            file=sys.stderr,
        )

    print(
        f"\n{'Would tag' if args.dry_run else 'Tagged'} {len(tagged)}/{len(tags)} questions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
