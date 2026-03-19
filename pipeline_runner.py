"""Pipeline step runners for the web app. Encapsulates config + pipeline call pattern."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from calibrate import run_calibration
from estimate import estimate_grade, estimate_rubrics
from export import export_all, export_autograder_zip
from gather import gather_submissions
from parse_notebook import parse_all_students
from parse_outputs import build_parse_run_payload
from utils import ensure_app_config

if TYPE_CHECKING:
    from config_models import AppConfig


def run_gather(config: AppConfig | dict, zip_path: Path) -> dict:
    """Run gather from ZIP. Returns results and output_dir."""
    cfg = ensure_app_config(config)
    out_dir = Path(cfg.submissions_dir)
    results = gather_submissions(zip_path, out_dir, from_zip=True)
    return {"results": results, "output_dir": str(out_dir)}


def run_gather_from_folder(
    config: AppConfig | dict, folder_path: str, project_root: Path
) -> dict:
    """Run gather from folder. Returns results and output_dir.
    Raises FileNotFoundError or ValueError if folder invalid."""
    cfg = ensure_app_config(config)
    out_dir = Path(cfg.submissions_dir)
    folder = Path(folder_path).resolve()
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    try:
        folder.relative_to(project_root)
    except ValueError:
        raise ValueError("Folder path must be inside the project directory.")
    results = gather_submissions(folder, out_dir, from_zip=False)
    return {"results": results, "output_dir": str(out_dir)}


def run_parse(config: AppConfig | dict) -> dict:
    """Run parse step. Returns report, preview, solution_questions, solution_duplicate_qids."""
    cfg = ensure_app_config(config)
    solution_parsed, report = parse_all_students(cfg)
    parsed_dir = Path(cfg.parsed_dir)
    return build_parse_run_payload(report, parsed_dir, solution_parsed)


def run_calibrate_step(config: AppConfig | dict) -> list:
    """Run calibration. Returns flagged list."""
    return run_calibration(config)


def get_calibration_report(config: AppConfig | dict) -> list:
    """Read calibration report from output_dir."""
    cfg = ensure_app_config(config)
    output_dir = Path(cfg.output_dir)
    path = output_dir / "calibration_report.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def run_export(config: AppConfig | dict) -> dict:
    """Run export. Returns summary."""
    return export_all(config)


def run_export_autograder_zip(config: AppConfig | dict) -> Path:
    """Create Gradescope autograder zip. Returns path."""
    return export_autograder_zip(config)


def run_estimate_rubrics(config: AppConfig | dict) -> dict:
    """Estimate rubric generation cost."""
    return estimate_rubrics(config)


def run_estimate_grade(config: AppConfig | dict, student_name: str | None = None) -> dict:
    """Estimate grading cost."""
    return estimate_grade(config, student_name=student_name)
