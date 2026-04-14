#!/usr/bin/env python3
"""Linter: check that submission notebook has required question labels. 0 pts.

This script runs on Gradescope's autograder infrastructure where the main
codebase is not available. It is self-contained by design. The summary
format intentionally mirrors ``linter_export.build_linter_summary()`` —
keep the two in sync when editing.

Placeholders ``{required_ids}`` and ``{question_regex}`` are replaced at
ZIP creation time by ``linter_export._build_run_autograder()``.
"""

import json
import re
from pathlib import Path

REQUIRED_IDS = {required_ids}
QUESTION_REGEX = re.compile({question_regex}, re.MULTILINE)

submission_dir = Path("/autograder/submission")
results_dir = Path("/autograder/results")
out_path = results_dir / "results.json"

results_dir.mkdir(parents=True, exist_ok=True)


def _fmt_list(items):
    if not items:
        return "(none)"
    return ", ".join(f"`{x}`" for x in items)


def _emit(summary, ok):
    payload = {
        "score": 0,
        "tests": [
            {
                "name": "Notebook Format Lint",
                "status": "passed" if ok else "failed",
                "score": 0,
                "max_score": 0,
                "visibility": "visible",
                "output_format": "md",
                "output": summary,
            }
        ],
    }
    with open(out_path, "w") as f:
        json.dump(payload, f)


# Find notebook
nb_files = list(submission_dir.glob("*.ipynb"))
if not nb_files:
    summary = "\n".join([
        "# Notebook Linter Summary",
        "",
        "Status: FAILED",
        "",
        "No `.ipynb` file found in submission.",
    ])
    _emit(summary, ok=False)
    raise SystemExit(0)

nb_path = nb_files[0]
nb = json.loads(nb_path.read_text(encoding="utf-8"))
cells = nb.get("cells", [])


def _sort_key(qid):
    parts = qid.split(".")
    if len(parts) == 2:
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    return (999999, qid)


found_counts = {}
for cell in cells:
    if cell.get("cell_type") != "markdown":
        continue
    text = "".join(cell.get("source", []))
    m = QUESTION_REGEX.search(text)
    if not m:
        continue
    if m.lastindex >= 4:
        sec_id, qnum = m.group(2), m.group(3)
    else:
        sec_id, qnum = m.group(1), m.group(2)
    qid = f"{sec_id}.{qnum}"
    found_counts[qid] = found_counts.get(qid, 0) + 1

found_ids = sorted(found_counts.keys(), key=_sort_key)
required_set = set(REQUIRED_IDS)
found_set = set(found_ids)
missing = sorted(required_set - found_set, key=_sort_key)
unexpected = sorted(found_set - required_set, key=_sort_key)
duplicates = sorted([qid for qid, cnt in found_counts.items() if cnt > 1], key=_sort_key)

dup_lines = [f"- `{qid}` appears **{found_counts[qid]}** times" for qid in duplicates]

ok = len(missing) == 0 and len(duplicates) == 0
summary_lines = [
    "# Notebook Linter Summary",
    "",
    f"Status: {'PASSED' if ok else 'FAILED'}",
    "",
    f"Notebook: `{nb_path.name}`",
    f"Required questions: **{len(REQUIRED_IDS)}**",
    f"Questions found: **{len(found_ids)}**",
    f"Questions missing: **{len(missing)}**",
    f"Duplicate labels: **{len(duplicates)}**",
    f"Unexpected question labels: **{len(unexpected)}**",
    "",
    "## Questions Found",
    _fmt_list(found_ids),
    "",
    "## Duplicates Found",
    "\n".join(dup_lines) if dup_lines else "(none)",
    "",
    "## Questions Missing",
    _fmt_list(missing),
    "",
    "## Unexpected Question Labels",
    _fmt_list(unexpected),
]
summary = "\n".join(summary_lines)
_emit(summary, ok=ok)
