"""Shared DTO builders for parse pipeline results (CLI + web app)."""

from __future__ import annotations

import json
from pathlib import Path

from parse_notebook import get_all_question_ids


def build_parse_run_payload(
    report: list,
    parsed_dir: Path,
    solution_parsed: dict | None,
) -> dict:
    """Shape returned by ``pipeline_runner.run_parse`` / parse API."""
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
