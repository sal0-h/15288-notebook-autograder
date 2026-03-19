"""Pipeline step runners for the web app. Encapsulates config + pipeline call pattern."""

from pathlib import Path

from calibrate import run_calibration
from estimate import estimate_grade, estimate_rubrics
from export import export_all, export_autograder_zip
from gather import gather_submissions
from parse_notebook import parse_all_students
from parse_outputs import build_parse_run_payload


def run_gather(config: dict, zip_path: Path) -> dict:
    """Run gather from ZIP. Returns results and output_dir."""
    out_dir = Path(config.get("submissions_dir", "output/submissions"))
    results = gather_submissions(zip_path, out_dir, from_zip=True)
    return {"results": results, "output_dir": str(out_dir)}


def run_gather_from_folder(config: dict, folder_path: str, project_root: Path) -> dict:
    """Run gather from folder. Returns results and output_dir.
    Raises FileNotFoundError or ValueError if folder invalid."""
    out_dir = Path(config.get("submissions_dir", "output/submissions"))
    folder = Path(folder_path).resolve()
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    try:
        folder.relative_to(project_root)
    except ValueError:
        raise ValueError("Folder path must be inside the project directory.")
    results = gather_submissions(folder, out_dir, from_zip=False)
    return {"results": results, "output_dir": str(out_dir)}


def run_parse(config: dict) -> dict:
    """Run parse step. Returns report, preview, solution_questions, solution_duplicate_qids."""
    solution_parsed, report = parse_all_students(config)
    parsed_dir = Path(config.get("parsed_dir", "output/parsed"))
    return build_parse_run_payload(report, parsed_dir, solution_parsed)


def run_calibrate_step(config: dict) -> list:
    """Run calibration. Returns flagged list."""
    return run_calibration(config)


def get_calibration_report(config: dict) -> list:
    """Read calibration report from output_dir."""
    output_dir = Path(config.get("output_dir", "output"))
    path = output_dir / "calibration_report.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def run_export(config: dict) -> dict:
    """Run export. Returns summary."""
    return export_all(config)


def run_export_autograder_zip(config: dict) -> Path:
    """Create Gradescope autograder zip. Returns path."""
    return export_autograder_zip(config)


def run_estimate_rubrics(config: dict) -> dict:
    """Estimate rubric generation cost."""
    return estimate_rubrics(config)


def run_estimate_grade(config: dict, student_name: str | None = None) -> dict:
    """Estimate grading cost."""
    return estimate_grade(config, student_name=student_name)
