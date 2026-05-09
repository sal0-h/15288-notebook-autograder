#!/usr/bin/env python3
"""
AI Autograder entry point.

Write config programmatically and run the pipeline with: python main.py

Override settings via CLI flags or by editing the CONFIG dict below.
"""

import argparse
import copy
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from config_models import default_config
from parse_notebook import get_all_question_ids, get_total_points
from pipeline_runner import (
    run_gather,
    run_genai_detection,
    run_parse,
)
from calibrate import run_calibration
from export import export_all
from config_models import load_app_config, sanitize_assignment_name
from config_models import AppConfig, get_assignment_output_paths, save_config
from utils import setup_assignment_logging


# -----------------------------------------------------------------------------
# Programmatic config — edit this to customize
# -----------------------------------------------------------------------------
def _apply_cli_runtime_overrides(cfg: AppConfig, args: argparse.Namespace) -> AppConfig:
    """Apply --model / --solution / --submissions-dir without requiring a config write."""
    updates: dict = {}
    if args.model:
        updates["model"] = args.model
    if args.solution:
        updates["solution_notebook"] = str(Path(args.solution).resolve())
    if args.submissions_dir:
        updates["submissions_dir"] = str(Path(args.submissions_dir).resolve())
    return cfg.model_copy(update=updates) if updates else cfg


CONFIG = default_config(
    "LabTest_3_S26",
    solution_notebook="archive1/LabTest_2_S26_sol.ipynb",
    grading={
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
)


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
  python main.py --steps grade --config output/S25_LabTest_2/config.yaml --no-write-config --model gpt-4.1

CLI overrides (--model, --solution, --submissions-dir) apply for this run even when --no-write-config is set.
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
        choices=[
            "gather",
            "parse",
            "generate-rubrics",
            "grade",
            "detect-genai",
            "calibrate",
            "export",
        ],
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
    try:
        cfg = load_app_config(config_path)
    except ValidationError as e:
        print(f"Invalid config: {e}")
        return 1

    cfg = _apply_cli_runtime_overrides(cfg, args)

    # Set up file logging to output_dir/autograder.log (same as web app)
    out_dir = Path(cfg.output_dir)
    assign_name = cfg.assignment_name
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
        gathered = run_gather(cfg, args.zip)
        results = gathered["results"]
        out_dir = Path(gathered["output_dir"])
        ok = sum(1 for r in results if r["status"] == "ok")
        print(f"Gather: {ok}/{len(results)} notebooks copied to {out_dir}")

    if "parse" in steps:
        payload = run_parse(cfg)
        report = payload["report"]
        paths = get_assignment_output_paths(cfg)
        if paths.solution_parsed.exists():
            solution_parsed = json.loads(
                paths.solution_parsed.read_text(encoding="utf-8")
            )
            qids = get_all_question_ids(solution_parsed)
            pts = get_total_points(solution_parsed)
            print(f"Parse: solution has {len(qids)} questions, {pts} pts")
        for r in report:
            icon = "✓" if r["status"] == "ok" else "⚠"
            print(
                f"  {icon} {r['student_name']}: {len(r.get('questions_found', []))} questions"
            )

    if "generate-rubrics" in steps:
        from rubric_generate import generate_rubrics

        rubrics = generate_rubrics(cfg)
        merged = cfg.model_dump(mode="python")
        merged["rubrics"] = {qid: e.model_dump() for qid, e in rubrics.items()}
        save_config(merged, config_path)
        cfg = _apply_cli_runtime_overrides(load_app_config(config_path), args)
        print(f"Generate rubrics: {len(rubrics)} questions")

    if "grade" in steps:
        from batch_grader import grade_all_students

        for evt in grade_all_students(cfg):
            if evt["status"] == "done" and evt.get("result"):
                r = evt["result"]
                print(f"  ✓ {r['student_name']}: {r['total_score']}/{r['total_max']}")
            elif evt["status"] == "error":
                print(f"  ✗ {evt['student']}: {evt['error']}")
        print("Grade: done")

    if "detect-genai" in steps:
        summary = run_genai_detection(cfg)
        print(
            f"GenAI detection: {summary['students_processed']} students processed, "
            f"{summary['questions_flagged']} question(s) flagged, "
            f"{summary['students_skipped']} skipped"
        )
        for err in summary.get("errors") or []:
            logging.warning("%s", err)

    if "calibrate" in steps:
        flagged = run_calibration(cfg)
        print(f"Calibrate: {len(flagged)} outlier(s) flagged")

    if "export" in steps:
        summary = export_all(cfg)
        print(f"Export: {summary['students']} students → {summary['excel_path']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
