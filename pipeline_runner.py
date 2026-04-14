"""Pipeline step runners for the web app. Encapsulates config + pipeline call pattern."""

from __future__ import annotations

import json
from pathlib import Path

from calibrate import run_calibration
from config_models import AppConfig
from estimate import estimate_grade, estimate_rubrics
from export import export_all, export_autograder_zip
from gather import gather_submissions
from parse_notebook import get_all_question_ids, parse_all_students


def run_gather(config: AppConfig, zip_path: Path) -> dict:
    """Run gather from ZIP. Returns results and output_dir."""
    out_dir = Path(config.submissions_dir)
    results = gather_submissions(zip_path, out_dir, from_zip=True)
    return {"results": results, "output_dir": str(out_dir)}


def run_gather_from_folder(
    config: AppConfig, folder_path: str, project_root: Path
) -> dict:
    """Run gather from folder. Returns results and output_dir.
    Raises FileNotFoundError or ValueError if folder invalid."""
    out_dir = Path(config.submissions_dir)
    folder = Path(folder_path).resolve()
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    try:
        folder.relative_to(project_root)
    except ValueError:
        raise ValueError("Folder path must be inside the project directory.")
    results = gather_submissions(folder, out_dir, from_zip=False)
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


def run_calibrate_step(config: AppConfig) -> list:
    """Run calibration. Returns flagged list."""
    return run_calibration(config)


def get_calibration_report(config: AppConfig) -> list:
    """Read calibration report from output_dir."""
    output_dir = Path(config.output_dir)
    path = output_dir / "calibration_report.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def run_export(config: AppConfig) -> dict:
    """Run export. Returns summary."""
    return export_all(config)


def run_export_autograder_zip(config: AppConfig) -> Path:
    """Create Gradescope autograder zip. Returns path."""
    return export_autograder_zip(config)


def run_estimate_rubrics(config: AppConfig) -> dict:
    """Estimate rubric generation cost."""
    return estimate_rubrics(config)


def run_estimate_grade(config: AppConfig, student_name: str | None = None) -> dict:
    """Estimate grading cost."""
    return estimate_grade(config, student_name=student_name)


def run_genai_detection(config: AppConfig) -> dict:
    """Run optional GenAI suspicion pass; merges flags into graded_results.json."""
    from genai_detection import run_genai_detection

    return run_genai_detection(config)
