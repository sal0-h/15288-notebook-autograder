#!/usr/bin/env python3
"""
AI Autograder entry point.

Write config programmatically and run the pipeline with: python main.py

Override settings via CLI flags or by editing the CONFIG dict below.
"""

import argparse
import copy
import logging
from pathlib import Path

from pydantic import ValidationError

from utils import (
    load_config,
    save_config,
    AppConfig,
    DEFAULT_MODEL,
    setup_assignment_logging,
    sanitize_assignment_name,
)

# -----------------------------------------------------------------------------
# Programmatic config — edit this to customize
# -----------------------------------------------------------------------------
CONFIG = {
    "assignment_name": "LabTest_3_S26",
    "model": DEFAULT_MODEL,
    "solution_notebook": "archive1/LabTest_2_S26_sol.ipynb",
    "output_dir": "output",
    "workers": 1,
    "max_prompt_tokens": 80_000,
    "max_completion_tokens": 4_096,
    "parsing": {
        "section_regex": r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
        "question_regex": r"(?i)^\s*-\s*Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
        "keep_images": True,
    },
    "grading": {
        "question_groups": [
            ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8", "1.9"],
            ["2.1", "2.2", "2.3"],
            ["3.1"],
            ["4.1", "4.2", "4.3"],
            ["5.1", "5.2", "5.3"],
            ["6.1", "6.2", "6.3", "6.4"],
            ["6.5", "6.6", "6.7", "6.8"],
            ["6.9", "6.10", "6.11", "6.12"],
            ["7.1", "7.2"],
            ["8.1", "8.2", "8.3", "8.4", "8.5", "8.6"],
            ["9.1", "9.2", "9.3"],
        ],
    },
}


def main():
    parser = argparse.ArgumentParser(
        description="AI Autograder: write config and run pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Write config, run parse + grade + export
  python main.py --zip export.zip   # Also run gather first
  python main.py --config-only      # Only write config.yaml, no pipeline
  python main.py --steps parse      # Only run parse step
  python main.py --model gpt-5.2    # Override model for final grading
        """,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to assignment config.yaml (default: output/{assignment_name}/config.yaml)",
    )
    parser.add_argument(
        "--config-only",
        action="store_true",
        help="Only write config file, do not run pipeline",
    )
    parser.add_argument(
        "--zip",
        type=Path,
        metavar="PATH",
        help="Gradescope export ZIP to gather submissions from",
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        choices=["gather", "parse", "generate-rubrics", "grade", "calibrate", "export"],
        default=["parse", "grade", "export"],
        help="Pipeline steps to run (default: parse grade export)",
    )
    parser.add_argument(
        "--model",
        help="Override model (e.g. gpt-5.2 for final grading)",
    )
    parser.add_argument(
        "--solution",
        type=Path,
        help="Override solution notebook path",
    )
    parser.add_argument(
        "--submissions-dir",
        type=Path,
        help="Override submissions directory",
    )
    parser.add_argument(
        "--no-write-config",
        action="store_true",
        help="Do not overwrite config.yaml (use existing file as-is)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    config = copy.deepcopy(CONFIG)

    # Apply overrides
    if args.model:
        config["model"] = args.model
    if args.solution:
        config["solution_notebook"] = str(args.solution)
    if args.submissions_dir:
        config["submissions_dir"] = str(args.submissions_dir)

    # Derive config path from assignment_name when not supplied
    safe_name = sanitize_assignment_name(config["assignment_name"])
    config_path: Path = args.config or (Path("output") / safe_name / "config.yaml")

    if not args.no_write_config:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config(config, config_path)
        print(f"Wrote config to {config_path}")
    config = load_config(config_path)  # Reload to resolve paths relative to config file

    try:
        AppConfig.model_validate(config)
    except ValidationError as e:
        print(f"Invalid config: {e}")
        return 1

    # Set up file logging to output_dir/autograder.log (same as web app)
    out_dir = Path(config["output_dir"])
    assign_name = config["assignment_name"]
    log_path = setup_assignment_logging(assign_name, out_dir)
    logging.info("Logging to %s", log_path)

    if args.config_only:
        return 0

    steps = list(args.steps)
    if args.zip and "gather" not in steps:
        steps.insert(0, "gather")

    # Run pipeline steps
    if "gather" in steps:
        if not args.zip:
            print("Error: --gather requires --zip PATH")
            return 1
        from gather import gather_submissions

        out_dir = Path(config["submissions_dir"])
        results = gather_submissions(args.zip, out_dir, from_zip=True)
        ok = sum(1 for r in results if r["status"] == "ok")
        print(f"Gather: {ok}/{len(results)} notebooks copied to {out_dir}")

    if "parse" in steps:
        from parse_notebook import (
            parse_all_students,
            get_all_question_ids,
            get_total_points,
        )

        solution_parsed, report = parse_all_students(config)
        if solution_parsed:
            qids = get_all_question_ids(solution_parsed)
            pts = get_total_points(solution_parsed)
            print(f"Parse: solution has {len(qids)} questions, {pts} pts")
        for r in report:
            icon = "✓" if r["status"] == "ok" else "⚠"
            print(
                f"  {icon} {r['student_name']}: {len(r.get('questions_found', []))} questions"
            )

    if "generate-rubrics" in steps:
        from rubric import generate_rubrics

        rubrics = generate_rubrics(config)
        config["rubrics"] = rubrics
        save_config(config, config_path)
        print(f"Generate rubrics: {len(rubrics)} questions")

    if "grade" in steps:
        from batch_grader import grade_all_students

        for evt in grade_all_students(config):
            if evt["status"] == "done" and evt.get("result"):
                r = evt["result"]
                print(f"  ✓ {r['student_name']}: {r['total_score']}/{r['total_max']}")
            elif evt["status"] == "error":
                print(f"  ✗ {evt['student']}: {evt['error']}")
        print("Grade: done")

    if "calibrate" in steps:
        from calibrate import run_calibration

        flagged = run_calibration(config)
        print(f"Calibrate: {len(flagged)} outlier(s) flagged")

    if "export" in steps:
        from export import export_all

        summary = export_all(config)
        print(f"Export: {summary['students']} students → {summary['excel_path']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
