"""Pipeline step runners for the web app. Encapsulates config + pipeline call pattern."""

from __future__ import annotations

import json
from pathlib import Path

from config_models import AppConfig
from gather import gather_submissions
from parse_notebook import get_all_question_ids, parse_all_students


def run_gather(config: AppConfig, zip_path: Path) -> dict:
    """Run gather from ZIP. Returns results and output_dir."""
    out_dir = Path(config.submissions_dir)
    results = gather_submissions(zip_path, out_dir, from_zip=True)
    return {"results": results, "output_dir": str(out_dir)}


def run_parse(config: AppConfig) -> dict:
    """Run parse step. Returns report, preview, solution_questions, solution_duplicate_qids."""
    solution_parsed, report = parse_all_students(config)
    parsed_dir = Path(config.parsed_dir)
    preview = None
    if report:
        first_student = report[0]["student_name"]
        preview_path = parsed_dir / f"{first_student}.json"
        if preview_path.exists():
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
    return {
        "report": report,
        "preview": preview,
        "solution_questions": (
            get_all_question_ids(solution_parsed) if solution_parsed else []
        ),
        "solution_duplicate_qids": (
            solution_parsed.get("duplicate_qids", []) if solution_parsed else []
        ),
    }


def get_calibration_report(config: AppConfig) -> list:
    """Read calibration report from output_dir."""
    output_dir = Path(config.output_dir)
    path = output_dir / "calibration_report.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def run_genai_detection(config: AppConfig) -> dict:
    """Run optional GenAI suspicion pass; merges flags into graded_results.json."""
    from genai_detection import run_genai_detection

    return run_genai_detection(config)
