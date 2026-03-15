"""Export graded results to Gradescope autograder JSON and Excel."""

import json
import zipfile
from pathlib import Path

import pandas as pd

from linter_export import build_linter_summary, fmt_qid_list
from parse_notebook import _sort_key_qid
from utils import AppConfig, ensure_app_config, load_config

# Unix executable bits used when creating Gradescope autograder zip entries
_UNIX_EXEC_ATTR = 0o755 << 16
_ZIP_UNIX_CREATE_SYSTEM = 3  # Unix

# Gradescope expects these at the root of the autograder zip
SETUP_SH = """#!/bin/bash
# No setup required - we only output pre-computed results
"""

RUN_AUTOGRADER = r'''#!/usr/bin/env python3
"""Output pre-computed AI autograder results for the current submission."""
import json
import shutil
from pathlib import Path

results_dir = Path("/autograder/results")
source_dir = Path("/autograder/source")
pre_computed = source_dir / "results"
metadata_path = Path("/autograder/submission_metadata.json")

results_dir.mkdir(parents=True, exist_ok=True)
out_path = results_dir / "results.json"

if not metadata_path.exists():
    with open(out_path, "w") as f:
        json.dump({"output": "Error: submission_metadata.json not found.", "tests": []}, f)
    exit(0)

with open(metadata_path) as f:
    meta = json.load(f)
users = meta.get("users", [])
student_name = (users[0].get("name", "") or "").strip() if users else ""

def _normalize(name: str) -> str:
    # Mirror export filename sanitization and avoid fuzzy matching collisions.
    return "".join(c for c in name.strip() if c not in '/\\:*?"<>|')

match_path = None
if student_name and pre_computed.exists():
    normalized_student = _normalize(student_name)
    matches = []
    for f in sorted(pre_computed.glob("*.json")):
        if _normalize(f.stem) == normalized_student:
            matches.append(f)
    if len(matches) == 1:
        match_path = matches[0]
    elif len(matches) > 1:
        with open(out_path, "w") as f:
            json.dump({
                "output": f"Ambiguous pre-computed results for: {student_name}. Found {len(matches)} exact-normalized matches.",
                "tests": []
            }, f)
        exit(0)

if match_path:
    shutil.copy(match_path, out_path)
else:
    with open(out_path, "w") as f:
        json.dump({
            "output": f"No pre-computed results for: {student_name}. Run AI autograder export first.",
            "tests": []
        }, f)
'''


def _build_linter_summary_test(
    student_name: str,
    parsed_dir: Path,
    required_qids: list[str],
) -> dict:
    """Build a single 0-pt linter test summary for one student."""
    parsed_path = parsed_dir / f"{student_name}.json"
    if not parsed_path.exists():
        summary, _ = build_linter_summary(
            found=[],
            required=sorted(required_qids, key=_sort_key_qid),
            missing=sorted(required_qids, key=_sort_key_qid),
            unexpected=[],
            duplicates=[],
        )
        summary = "\n".join(
            [
                "# Notebook Linter Summary",
                "",
                "Status: FAILED",
                "",
                f"Parsed notebook not found for `{student_name}`.",
                "Cannot compute found/missing/duplicate question labels.",
            ]
        )
        return {
            "name": "Notebook Format Lint",
            "score": 0,
            "max_score": 0,
            "output": summary,
            "output_format": "md",
            "visibility": "visible",
        }

    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    found_set: set[str] = set()
    for sec_data in (parsed.get("sections") or {}).values():
        found_set.update((sec_data.get("questions") or {}).keys())

    found = sorted(found_set, key=_sort_key_qid)
    required = sorted(set(required_qids), key=_sort_key_qid)
    missing = sorted(set(required) - found_set, key=_sort_key_qid)
    unexpected = sorted(found_set - set(required), key=_sort_key_qid)
    duplicates = sorted(parsed.get("duplicate_qids") or [], key=_sort_key_qid)

    summary, _ = build_linter_summary(found, required, missing, unexpected, duplicates)
    return {
        "name": "Notebook Format Lint",
        "score": 0,
        "max_score": 0,
        "output": summary,
        "output_format": "md",
        "visibility": "visible",
    }


def export_all(config: AppConfig | dict) -> dict[str, str | int]:
    """
    Read graded_results.json and write:
    - output/gradescope/{StudentName}.json (Gradescope autograder format)
    - output/Final_Grades.xlsx (human-readable Excel)

    Returns summary dict with paths and counts.
    """
    cfg = ensure_app_config(config)
    output_dir = Path(cfg.output_dir)
    parsed_dir = Path(cfg.parsed_dir)
    graded_path = output_dir / "graded_results.json"
    gradescope_dir = output_dir / "gradescope"

    if not graded_path.exists():
        raise FileNotFoundError(f"Graded results not found: {graded_path}")

    results = json.loads(graded_path.read_text(encoding="utf-8"))
    if not results:
        return {"students": 0, "gradescope_dir": str(gradescope_dir), "excel_path": ""}

    gradescope_dir.mkdir(parents=True, exist_ok=True)

    # Collect all question IDs for Excel columns
    all_qids: set[str] = set()
    for r in results:
        all_qids.update(r.get("questions", {}).keys())
    q_cols = sorted(all_qids, key=_sort_key_qid)

    # For Gradescope: only include grade_only questions if set; use optional title mapping
    grade_only = cfg.grading.grade_only
    gs_title_mapping = cfg.gradescope_title_mapping

    if grade_only:
        gs_q_cols = [q for q in grade_only if q in all_qids]
        gs_q_cols = sorted(gs_q_cols, key=_sort_key_qid)
    else:
        gs_q_cols = q_cols

    # Export Gradescope JSON per student
    for r in results:
        student_name = r.get("student_name", "Unknown")
        questions = r.get("questions", {})

        tests = []
        for qid in gs_q_cols:
            q_data = questions.get(qid, {"score": 0, "max": 0, "feedback": ""})
            tests.append(
                {
                    "name": gs_title_mapping.get(qid, qid),
                    "score": q_data.get("score", 0),
                    "max_score": q_data.get("max", 0),
                    "output": q_data.get("feedback", ""),
                    "output_format": "md",
                    "visibility": "visible",
                }
            )

        # Keep linter summary as the last test case for quick format diagnostics.
        tests.append(_build_linter_summary_test(student_name, parsed_dir, gs_q_cols))

        gs_data = {"tests": tests}
        safe_name = (
            "".join(c for c in student_name if c not in '/\\:*?"<>|')
            or "unknown_student"
        )
        gs_path = gradescope_dir / f"{safe_name}.json"
        gs_path.write_text(json.dumps(gs_data, indent=2), encoding="utf-8")

    # Export Excel
    rows = []
    for r in results:
        row = {
            "student_name": r.get("student_name", ""),
            "total_score": r.get("total_score", 0),
        }
        for qid in q_cols:
            q_data = r.get("questions", {}).get(qid, {})
            row[f"Q{qid}"] = q_data.get("score", "")
        row["summary_feedback"] = r.get("summary_feedback", "")
        rows.append(row)

    cols = (
        ["student_name", "total_score"]
        + [f"Q{q}" for q in q_cols]
        + ["summary_feedback"]
    )
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    excel_path = output_dir / "Final_Grades.xlsx"
    df.to_excel(excel_path, index=False)

    # Always regenerate autograder zip so it stays in sync with grades
    zip_path = export_autograder_zip(config)

    return {
        "students": len(results),
        "gradescope_dir": str(gradescope_dir),
        "gradescope_files": len(results),
        "excel_path": str(excel_path),
        "autograder_zip": str(zip_path),
    }


def export_autograder_zip(config: AppConfig | dict) -> Path:
    """
    Create a Gradescope autograder zip that outputs pre-computed results.
    Run export_all first to ensure gradescope/*.json exist.

    Returns path to the created zip file.
    """
    cfg = ensure_app_config(config)
    output_dir = Path(cfg.output_dir)
    gradescope_dir = output_dir / "gradescope"
    zip_path = output_dir / "gradescope_autograder.zip"

    if not gradescope_dir.exists():
        raise FileNotFoundError(
            f"Gradescope results not found: {gradescope_dir}. Run export first."
        )

    json_files = list(gradescope_dir.glob("*.json"))
    if not json_files:
        raise FileNotFoundError(f"No JSON files in {gradescope_dir}. Run export first.")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo("setup.sh")
        zi.create_system = _ZIP_UNIX_CREATE_SYSTEM
        zi.external_attr = _UNIX_EXEC_ATTR
        zf.writestr(zi, SETUP_SH)
        zi = zipfile.ZipInfo("run_autograder")
        zi.create_system = _ZIP_UNIX_CREATE_SYSTEM
        zi.external_attr = _UNIX_EXEC_ATTR
        zf.writestr(zi, RUN_AUTOGRADER)
        for jf in json_files:
            arcname = f"results/{jf.name}"
            zf.write(jf, arcname=arcname)

    return zip_path


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to assignment config.yaml (required, typically output/{assignment_name}/config.yaml)",
    )
    parser.add_argument(
        "--autograder-zip",
        action="store_true",
        help="Create Gradescope autograder zip (run export first if needed)",
    )
    args = parser.parse_args()
    if args.config is None:
        parser.error("--config is required and must point to output/{assignment_name}/config.yaml")

    config = load_config(args.config)
    if args.autograder_zip:
        export_all(config)  # Ensure gradescope/*.json exist
        zip_path = export_autograder_zip(config)
        print(f"Created: {zip_path}")
        print("Upload this zip in Gradescope: Configure Autograder → Upload")
        print("Then use Manage Submissions → Rerun Autograders")
    else:
        summary = export_all(config)
        print(f"Exported {summary['students']} students")
        print(f"Gradescope JSONs: {summary['gradescope_dir']}")
        print(f"Excel: {summary['excel_path']}")
        print(
            "To create autograder zip: python export.py --config ... --autograder-zip"
        )


if __name__ == "__main__":
    main()
