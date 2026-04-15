"""Graded results and parsed notebooks for review UI."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException

from api.helpers import safe_path
from api import state
from api.helpers import require_active_config
from api.validation import parse_student_name_path_param
from results_models import GradedResult
from results_store import load_results, save_results, update_student
from config_models import get_assignment_output_paths

router = APIRouter()


@router.get("/results")
def api_get_results():
    """Return full graded_results.json."""
    cfg = require_active_config()
    path = get_assignment_output_paths(cfg).graded_results
    with state.results_lock:
        try:
            return load_results(path)
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.put("/results/{student_name:path}")
def api_put_results(student_name: str, result: dict = Body(...)):
    """Update one student's scores in graded_results.json."""
    student_name = parse_student_name_path_param(student_name)
    required = ("student_name", "questions", "total_score", "total_max")
    if not all(k in result for k in required):
        raise HTTPException(
            status_code=422,
            detail=f"Missing required keys: {[k for k in required if k not in result]}",
        )

    # Validate and convert dict to GradedResult
    try:
        validated = GradedResult.model_validate(result)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid result schema: {str(e)}")

    cfg = require_active_config()
    path = get_assignment_output_paths(cfg).graded_results

    if not path.exists():
        raise HTTPException(status_code=404, detail="No graded results yet")

    with state.results_lock:
        try:
            results = load_results(path)
            update_student(results, student_name, validated)
            save_results(path, results)
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@router.get("/parsed/{student_name:path}")
def api_get_parsed(student_name: str):
    """Return parsed JSON for a specific student."""
    student_name = parse_student_name_path_param(student_name)
    cfg = require_active_config()
    parsed_dir = Path(cfg.parsed_dir)
    path = safe_path(parsed_dir, f"{student_name}.json")
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Parsed notebook not found for: {student_name}"
        )
    return json.loads(path.read_text(encoding="utf-8"))
