"""Export graded results to Gradescope autograder JSON and Excel."""

import json
import zipfile
from pathlib import Path

import pandas as pd

from config_models import AppConfig, sort_key_qid, load_app_config
from gradescope_submitters import (
    MANIFEST_SCHEMA_VERSION,
    submitter_key_to_results_filename,
)
from linter_export import build_linter_summary
from parse_notebook import get_all_question_ids
from results_models import GradedResult, Question
from utils import (
    get_assignment_output_paths,
    sanitize_filename_component,
)
from zip_helpers import write_to_zip

_REPO_ROOT = Path(__file__).resolve().parent
_GRADESCOPE_PY_MODULES = ("gradescope_submitters.py", "gradescope_runtime.py")

RUN_AUTOGRADER = """#!/usr/bin/env python3
import sys
sys.path.insert(0, "/autograder/source")
from gradescope_runtime import main

if __name__ == "__main__":
    main()
"""


def _extract_question_scores(q: Question | dict) -> tuple[float, float, str]:
    if isinstance(q, dict):
        return (
            float(q.get("score", 0)),
            float(q.get("max", 0)),
            str(q.get("feedback", "")),
        )
    return (float(q.score), float(q.max), str(q.feedback or ""))


def _stem_to_submitter_key_map(stem_map: dict[str, str]) -> dict[str, str]:
    """Invert submitter_key → notebook stem (stem is graded ``student_name`` / file stem)."""
    out: dict[str, str] = {}
    for key, stem in stem_map.items():
        prev = out.get(stem)
        if prev is not None and prev != key:
            raise ValueError(
                f"submitter_stem_map maps multiple keys to the same stem {stem!r}: "
                f"{prev!r} vs {key!r}"
            )
        out[stem] = key
    return out


def build_precomputed_manifest(
    submitter_stem_map: dict[str, str],
    gradescope_json_stems: list[str],
    *,
    assignment_id: int | None,
    course_id: int | None,
) -> dict | None:
    """
    Build ``precomputed_manifest.json`` payload for the autograder ZIP.

    Returns None when there is no id-level map or no matching graded stems.
    """
    if not submitter_stem_map:
        return None
    stem_to_key = _stem_to_submitter_key_map(submitter_stem_map)
    entries: dict[str, dict[str, str]] = {}
    for stem in gradescope_json_stems:
        key = stem_to_key.get(stem)
        if not key:
            continue
        fname = f"{submitter_key_to_results_filename(key)}.json"
        entries[key] = {"file": f"results/{fname}", "display_name": stem}
    if not entries:
        return None
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "assignment_id": assignment_id,
        "course_id": course_id,
        "entries": entries,
    }


# Gradescope expects these at the root of the autograder zip
SETUP_SH = """#!/bin/bash
# No setup required - we only output pre-computed results
"""


def _linter_test_entry(summary: str) -> dict:
    """Wrap a linter summary string into a 0-pt Gradescope test entry."""
    return {
        "name": "Notebook Format Lint",
        "score": 0,
        "max_score": 0,
        "output": summary,
        "output_format": "md",
        "visibility": "visible",
    }


def _build_linter_summary_test(
    student_name: str,
    parsed_dir: Path,
    required_qids: list[str],
) -> dict:
    """Build a single 0-pt linter test summary for one student."""
    parsed_path = parsed_dir / f"{student_name}.json"
    if not parsed_path.exists():
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
        return _linter_test_entry(summary)

    parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
    found_set: set[str] = set()
    for sec_data in (parsed.get("sections") or {}).values():
        found_set.update((sec_data.get("questions") or {}).keys())

    found = sorted(found_set, key=sort_key_qid)
    required = sorted(set(required_qids), key=sort_key_qid)
    missing = sorted(set(required) - found_set, key=sort_key_qid)
    unexpected = sorted(found_set - set(required), key=sort_key_qid)
    duplicates = sorted(parsed.get("duplicate_qids") or [], key=sort_key_qid)

    summary, _ = build_linter_summary(found, required, missing, unexpected, duplicates)
    return _linter_test_entry(summary)


def _export_gradescope_json(
    results: list[GradedResult],
    gs_q_cols: list[str],
    gs_title_mapping: dict[str, str],
    gradescope_dir: Path,
    parsed_dir: Path,
    required_qids_for_linter: list[str],
) -> int:
    """
    Export per-student Gradescope JSON files with tests and linter summary.

    Returns the number of files written.
    """
    for r in results:
        student_name = r.student_name or "Unknown"
        questions = r.questions

        tests = []
        for qid in gs_q_cols:
            q_data = questions.get(qid)
            score, max_s, out = (
                _extract_question_scores(q_data) if q_data is not None else (0.0, 0.0, "")
            )
            tests.append(
                {
                    "name": gs_title_mapping.get(qid, qid),
                    "score": score,
                    "max_score": max_s,
                    "output": out,
                    "output_format": "md",
                    "visibility": "visible",
                }
            )

        # Keep linter summary as the last test case for quick format diagnostics.
        tests.append(
            _build_linter_summary_test(
                student_name, parsed_dir, required_qids_for_linter
            )
        )

        gs_data = {"tests": tests}
        safe_name = sanitize_filename_component(
            student_name, if_empty="unknown_student"
        )
        gs_path = gradescope_dir / f"{safe_name}.json"
        gs_path.write_text(json.dumps(gs_data, indent=2), encoding="utf-8")

    return len(results)


def _export_excel(results: list[GradedResult], q_cols: list[str], output_dir: Path) -> str:
    """
    Export results to Excel (pandas DataFrame) and write to Final_Grades.xlsx.

    Returns the path to the Excel file as a string.
    """
    rows = []
    for r in results:
        row = {
            "student_name": r.student_name,
            "total_score": r.total_score,
        }
        for qid in q_cols:
            q_data = r.questions.get(qid)
            row[f"Q{qid}"] = "" if q_data is None else _extract_question_scores(q_data)[0]
        row["summary_feedback"] = r.summary_feedback
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

    return str(excel_path)


def export_all(cfg: AppConfig) -> dict[str, str | int]:
    """
    Read graded_results.json and write:
    - output/gradescope/{StudentName}.json (Gradescope autograder format)
    - output/Final_Grades.xlsx (human-readable Excel)

    Returns summary dict with paths and counts.
    """
    paths = get_assignment_output_paths(cfg)
    output_dir = paths.output_dir
    parsed_dir = paths.parsed_dir
    graded_path = paths.graded_results
    gradescope_dir = paths.gradescope_dir

    if not graded_path.exists():
        raise FileNotFoundError(f"Graded results not found: {graded_path}")

    raw_results = json.loads(graded_path.read_text(encoding="utf-8"))
    if not raw_results:
        return {
            "students": 0,
            "gradescope_dir": str(gradescope_dir),
            "gradescope_files": 0,
            "excel_path": "",
            "autograder_zip": "",
        }

    results: list[GradedResult] = [GradedResult.model_validate(r) for r in raw_results]
    gradescope_dir.mkdir(parents=True, exist_ok=True)

    # Collect all question IDs for Excel columns
    all_qids: set[str] = set()
    for r in results:
        all_qids.update(r.questions.keys())
    q_cols = sorted(all_qids, key=sort_key_qid)

    # For Gradescope: only include grade_only questions if set; use optional title mapping
    grade_only = cfg.grading.grade_only
    gs_title_mapping = cfg.gradescope_title_mapping
    if grade_only:
        gs_q_cols = [q for q in grade_only if q in all_qids]
        gs_q_cols = sorted(gs_q_cols, key=sort_key_qid)
    else:
        gs_q_cols = q_cols

    # Linter test uses full solution QIDs (not grade_only) when available
    solution_path = paths.solution_parsed
    if solution_path.exists():
        solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
        required_qids_for_linter = get_all_question_ids(solution_parsed)
    else:
        required_qids_for_linter = gs_q_cols

    # Export Gradescope JSON and Excel
    _export_gradescope_json(
        results, gs_q_cols, gs_title_mapping, gradescope_dir, parsed_dir, required_qids_for_linter
    )
    excel_path = _export_excel(results, q_cols, output_dir)

    # Always regenerate autograder zip so it stays in sync with grades
    zip_path = export_autograder_zip(cfg)

    return {
        "students": len(results),
        "gradescope_dir": str(gradescope_dir),
        "gradescope_files": len(results),
        "excel_path": excel_path,
        "autograder_zip": str(zip_path),
    }


def export_autograder_zip(config: AppConfig) -> Path:
    """
    Create a Gradescope autograder zip that outputs pre-computed results.
    Run export_all first to ensure gradescope/*.json exist.

    Returns path to the created zip file.
    """

    paths = get_assignment_output_paths(config)
    output_dir = paths.output_dir
    gradescope_dir = paths.gradescope_dir
    zip_path = output_dir / "gradescope_autograder.zip"

    if not gradescope_dir.exists():
        raise FileNotFoundError(
            f"Gradescope results not found: {gradescope_dir}. Run export first."
        )

    json_files = sorted(gradescope_dir.glob("*.json"))
    if not json_files:
        raise FileNotFoundError(f"No JSON files in {gradescope_dir}. Run export first.")

    stem_map_path = output_dir / "submitter_stem_map.json"
    submitter_stem_map: dict[str, str] = {}
    if stem_map_path.is_file():
        loaded = json.loads(stem_map_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            submitter_stem_map = {str(k): str(v) for k, v in loaded.items()}

    stems = [p.stem for p in json_files]
    manifest = build_precomputed_manifest(
        submitter_stem_map,
        stems,
        assignment_id=config.gradescope_assignment_id,
        course_id=config.gradescope_course_id,
    )
    stem_to_key = (
        _stem_to_submitter_key_map(submitter_stem_map) if submitter_stem_map else {}
    )

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        write_to_zip(zf, "setup.sh", SETUP_SH, executable=True)
        write_to_zip(zf, "run_autograder", RUN_AUTOGRADER, executable=True)
        for mod in _GRADESCOPE_PY_MODULES:
            mod_path = _REPO_ROOT / mod
            write_to_zip(zf, mod, mod_path.read_text(encoding="utf-8"))
        if manifest:
            write_to_zip(zf, "precomputed_manifest.json", json.dumps(manifest, indent=2))
        map_path = output_dir / "student_name_map.json"
        if map_path.exists():
            zf.write(map_path, arcname="student_name_map.json")
        for jf in json_files:
            human_arc = f"results/{jf.name}"
            zf.write(jf, arcname=human_arc)
            key = stem_to_key.get(jf.stem)
            if key:
                id_arc = f"results/{submitter_key_to_results_filename(key)}.json"
                if id_arc != human_arc:
                    zf.write(jf, arcname=id_arc)

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
        parser.error(
            "--config is required and must point to output/{assignment_name}/config.yaml"
        )

    config = load_app_config(args.config)
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
