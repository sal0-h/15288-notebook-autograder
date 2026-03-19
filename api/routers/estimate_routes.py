"""Token/cost estimate endpoints."""

from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, HTTPException

from api import state
from pipeline_runner import run_estimate_grade, run_estimate_rubrics

router = APIRouter()


@router.get("/estimate/rubrics")
def api_estimate_rubrics():
    """Estimate tokens and cost for rubric generation."""
    cfg = state.get_active_app_config()
    return run_estimate_rubrics(cfg)


@router.get("/estimate/grade")
def api_estimate_grade_all():
    """Estimate tokens and cost for grading all students."""
    cfg = state.get_active_app_config()
    return run_estimate_grade(cfg)


@router.get("/estimate/grade/{student_name:path}")
def api_estimate_grade_one(student_name: str):
    """Estimate tokens and cost for re-grading one student."""
    student_name = unquote(student_name)
    if "/" in student_name or "\\" in student_name or ".." in student_name:
        raise HTTPException(status_code=400, detail="Invalid student name")
    cfg = state.get_active_app_config()
    return run_estimate_grade(cfg, student_name=student_name)
