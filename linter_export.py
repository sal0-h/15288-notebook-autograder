"""Create a Phase 1 linter autograder zip for Gradescope (pre-deadline format check, 0 pts)."""

import json
import zipfile
from pathlib import Path

from parse_notebook import get_all_question_ids, parse_notebook
from config_models import load_app_config

# Unix executable bits used when creating Gradescope autograder zip entries
_UNIX_EXEC_ATTR = 0o755 << 16
_ZIP_UNIX_CREATE_SYSTEM = 3  # Unix

LINTER_SETUP_SH = """#!/bin/bash
# Python 3 is available on Gradescope; no setup required
"""


def _build_run_autograder(required_ids: list[str], question_regex: str) -> str:
    """Generate the run_autograder script with embedded required IDs and regex."""
    # Escape for embedding in a Python string (we use repr for the regex)
    ids_repr = repr(required_ids)
    regex_repr = repr(question_regex)

    return f'''#!/usr/bin/env python3
"""Linter: check that submission notebook has required question labels. 0 pts."""
import json
import re
from pathlib import Path

REQUIRED_IDS = {ids_repr}
QUESTION_REGEX = re.compile({regex_repr}, re.MULTILINE)

submission_dir = Path("/autograder/submission")
results_dir = Path("/autograder/results")
out_path = results_dir / "results.json"

results_dir.mkdir(parents=True, exist_ok=True)


def _fmt_list(items):
    if not items:
        return "(none)"
    return ", ".join(f"`{{x}}`" for x in items)


def _emit(summary, ok):
    payload = {{
        "score": 0,
        "tests": [
            {{
                "name": "Notebook Format Lint",
                "status": "passed" if ok else "failed",
                "score": 0,
                "max_score": 0,
                "visibility": "visible",
                "output_format": "md",
                "output": summary,
            }}
        ],
    }}
    with open(out_path, "w") as f:
        json.dump(payload, f)


# Find notebook
nb_files = list(submission_dir.glob("*.ipynb"))
if not nb_files:
    summary = "\\n".join([
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

# Collect all Q IDs found in markdown cells (one match per cell, same as parse_notebook).
def _sort_key(qid):
    parts = qid.split(".")
    if len(parts) == 2:
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    return (999999, qid)

found_counts = {{}}
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
    qid = f"{{sec_id}}.{{qnum}}"
    found_counts[qid] = found_counts.get(qid, 0) + 1

found_ids = sorted(found_counts.keys(), key=_sort_key)
required_set = set(REQUIRED_IDS)
found_set = set(found_ids)
missing = sorted(required_set - found_set, key=_sort_key)
unexpected = sorted(found_set - required_set, key=_sort_key)
duplicates = sorted([qid for qid, cnt in found_counts.items() if cnt > 1], key=_sort_key)

dup_lines = [f"- `{{qid}}` appears **{{found_counts[qid]}}** times" for qid in duplicates]

ok = len(missing) == 0 and len(duplicates) == 0
summary_lines = [
    "# Notebook Linter Summary",
    "",
    f"Status: {{'PASSED' if ok else 'FAILED'}}",
    "",
    f"Notebook: `{{nb_path.name}}`",
    f"Required questions: **{{len(REQUIRED_IDS)}}**",
    f"Questions found: **{{len(found_ids)}}**",
    f"Questions missing: **{{len(missing)}}**",
    f"Duplicate labels: **{{len(duplicates)}}**",
    f"Unexpected question labels: **{{len(unexpected)}}**",
    "",
    "## Questions Found",
    _fmt_list(found_ids),
    "",
    "## Duplicates Found",
    "\\n".join(dup_lines) if dup_lines else "(none)",
    "",
    "## Questions Missing",
    _fmt_list(missing),
    "",
    "## Unexpected Question Labels",
    _fmt_list(unexpected),
]
summary = "\\n".join(summary_lines)
_emit(summary, ok=ok)
'''


def fmt_qid_list(items: list[str]) -> str:
    """Format a list of question IDs as a markdown inline list, or '(none)'."""
    if not items:
        return "(none)"
    return ", ".join(f"`{q}`" for q in items)


def build_linter_summary(
    found: list[str],
    required: list[str],
    missing: list[str],
    unexpected: list[str],
    duplicates: list[str],
    notebook_name: str = "",
) -> tuple[str, bool]:
    """
    Build the linter markdown summary string and pass/fail status.
    Returns (summary_str, ok).
    """
    ok = len(missing) == 0 and len(duplicates) == 0
    lines = [
        "# Notebook Linter Summary",
        "",
        f"Status: {'PASSED' if ok else 'FAILED'}",
        "",
    ]
    if notebook_name:
        lines += [f"Notebook: `{notebook_name}`", ""]
    lines += [
        f"Required questions: **{len(required)}**",
        f"Questions found: **{len(found)}**",
        f"Questions missing: **{len(missing)}**",
        f"Duplicate labels: **{len(duplicates)}**",
        f"Unexpected question labels: **{len(unexpected)}**",
        "",
        "## Questions Found",
        fmt_qid_list(found),
        "",
        "## Duplicates Found",
        fmt_qid_list(duplicates),
        "",
        "## Questions Missing",
        fmt_qid_list(missing),
        "",
        "## Unexpected Question Labels",
        fmt_qid_list(unexpected),
    ]
    return "\n".join(lines), ok


def export_linter_zip(config_path: Path | None = None) -> Path:
    """
    Create linter_autograder.zip for pre-deadline format validation.

    Loads config, parses solution to get required Q IDs, embeds them and the
    question_regex in run_autograder, writes the zip with create_system=3 and
    executable bits for Gradescope.

    Returns path to the created zip file.
    """
    if config_path is None:
        raise ValueError("config_path is required")
    cfg = load_app_config(config_path)
    output_dir = Path(cfg.output_dir)
    solution_path = Path(cfg.solution_notebook)

    if not solution_path or not solution_path.exists():
        raise FileNotFoundError(
            f"Solution notebook not found: {solution_path}. Set solution_notebook in config."
        )

    solution_parsed = parse_notebook(solution_path, cfg)
    required_ids = get_all_question_ids(solution_parsed)
    if not required_ids:
        raise ValueError("Solution has no questions; cannot build linter.")

    question_regex = cfg.parsing.question_regex

    run_autograder = _build_run_autograder(required_ids, question_regex)
    zip_path = output_dir / "linter_autograder.zip"
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo("setup.sh")
        zi.create_system = _ZIP_UNIX_CREATE_SYSTEM
        zi.external_attr = _UNIX_EXEC_ATTR
        zf.writestr(zi, LINTER_SETUP_SH)

        zi = zipfile.ZipInfo("run_autograder")
        zi.create_system = _ZIP_UNIX_CREATE_SYSTEM
        zi.external_attr = _UNIX_EXEC_ATTR
        zf.writestr(zi, run_autograder)

    return zip_path


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Create linter_autograder.zip for Gradescope pre-deadline format check"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to assignment config.yaml (required, typically output/{assignment_name}/config.yaml)",
    )
    args = parser.parse_args()
    if args.config is None:
        parser.error(
            "--config is required and must point to output/{assignment_name}/config.yaml"
        )

    zip_path = export_linter_zip(args.config)
    print(f"Created: {zip_path}")
    print(
        "Upload this zip in Gradescope before the deadline for format validation (0 pts)."
    )
    print("After the deadline, replace with gradescope_autograder.zip from Export.")


if __name__ == "__main__":
    main()
