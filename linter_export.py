"""Create a Phase 1 linter autograder zip for Gradescope (pre-deadline format check, 0 pts)."""

import json
import zipfile
from pathlib import Path

from parse_notebook import get_all_question_ids, parse_notebook
from utils import load_config

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

# Find notebook
nb_files = list(submission_dir.glob("*.ipynb"))
if not nb_files:
    with open(out_path, "w") as f:
        json.dump({{
            "score": 0,
            "output": "No .ipynb file found in submission.",
            "tests": [{{"name": "No notebook", "status": "failed", "output": "No .ipynb found"}}]
        }}, f)
    exit(0)

nb_path = nb_files[0]
nb = json.loads(nb_path.read_text(encoding="utf-8"))
cells = nb.get("cells", [])

# Collect all Q IDs found in markdown cells using the same regex as main pipeline
found_ids = set()
for cell in cells:
    if cell.get("cell_type") != "markdown":
        continue
    text = "".join(cell.get("source", []))
    for m in QUESTION_REGEX.finditer(text):
        if m.lastindex >= 4:
            sec_id, qnum = m.group(2), m.group(3)
        else:
            sec_id, qnum = m.group(1), m.group(2)
        found_ids.add(f"{{sec_id}}.{{qnum}}")

# One test per required ID: Found Qx.y (passed) or Missing Qx.y (failed)
tests = []
for qid in REQUIRED_IDS:
    if qid in found_ids:
        tests.append({{"name": f"Found Q{{qid}}", "status": "passed", "score": 0, "max_score": 0}})
    else:
        tests.append({{"name": f"Missing Q{{qid}}", "status": "failed", "score": 0, "max_score": 0}})

with open(out_path, "w") as f:
    json.dump({{"score": 0, "tests": tests}}, f)
'''


def export_linter_zip(config_path: Path | None = None) -> Path:
    """
    Create linter_autograder.zip for pre-deadline format validation.

    Loads config, parses solution to get required Q IDs, embeds them and the
    question_regex in run_autograder, writes the zip with create_system=3 and
    executable bits for Gradescope.

    Returns path to the created zip file.
    """
    config = load_config(config_path)
    output_dir = Path(config.get("output_dir", "output"))
    solution_path = Path(config.get("solution_notebook", ""))

    if not solution_path or not solution_path.exists():
        raise FileNotFoundError(
            f"Solution notebook not found: {solution_path}. Set solution_notebook in config."
        )

    solution_parsed = parse_notebook(solution_path, config)
    required_ids = get_all_question_ids(solution_parsed)
    if not required_ids:
        raise ValueError("Solution has no questions; cannot build linter.")

    parsing = config.get("parsing", {})
    question_regex = parsing.get(
        "question_regex",
        r"(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
    )

    run_autograder = _build_run_autograder(required_ids, question_regex)
    zip_path = output_dir / "linter_autograder.zip"
    output_dir.mkdir(parents=True, exist_ok=True)

    exec_attr = 0o755 << 16
    create_system = 3  # Unix

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo("setup.sh")
        zi.create_system = create_system
        zi.external_attr = exec_attr
        zf.writestr(zi, LINTER_SETUP_SH)

        zi = zipfile.ZipInfo("run_autograder")
        zi.create_system = create_system
        zi.external_attr = exec_attr
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
        help="Path to config (default: config.yaml or output/{assignment}/config.yaml)",
    )
    args = parser.parse_args()

    zip_path = export_linter_zip(args.config)
    print(f"Created: {zip_path}")
    print("Upload this zip in Gradescope before the deadline for format validation (0 pts).")
    print("After the deadline, replace with gradescope_autograder.zip from Export.")


if __name__ == "__main__":
    main()
