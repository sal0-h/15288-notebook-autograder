"""Graded results and parsed notebooks for review UI."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Body, HTTPException

from api.helpers import safe_path
from api import state
from results_store import load_results, save_results, update_student
from utils import get_assignment_output_paths

router = APIRouter()


@router.get("/results")
def api_get_results():
    """Return full graded_results.json."""
    cfg = state.get_active_app_config()
    path = get_assignment_output_paths(cfg).graded_results
    with state.results_lock:
        try:
            return load_results(path)
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.put("/results/{student_name:path}")
def api_put_results(student_name: str, result: dict = Body(...)):
    """Update one student's scores in graded_results.json."""
    student_name = unquote(student_name)
    if "/" in student_name or "\\" in student_name or ".." in student_name:
        raise HTTPException(status_code=400, detail="Invalid student name")
    required = ("student_name", "questions", "total_score", "total_max")
    if not all(k in result for k in required):
        raise HTTPException(
            status_code=422,
            detail=f"Missing required keys: {[k for k in required if k not in result]}",
        )
    cfg = state.get_active_app_config()
    path = get_assignment_output_paths(cfg).graded_results

    if not path.exists():
        raise HTTPException(status_code=404, detail="No graded results yet")

    with state.results_lock:
        try:
            results = load_results(path)
            update_student(results, student_name, result)
            save_results(path, results)
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@router.get("/parsed/{student_name:path}")
def api_get_parsed(student_name: str):
    """Return parsed JSON for a specific student."""
    student_name = unquote(student_name)
    cfg = state.get_active_app_config()
    parsed_dir = Path(cfg.parsed_dir)
    path = safe_path(parsed_dir, f"{student_name}.json")
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Parsed notebook not found for: {student_name}"
        )
    return json.loads(path.read_text(encoding="utf-8"))
