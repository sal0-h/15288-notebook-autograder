"""Token/cost estimate endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api import state
from api.helpers import require_active_config
from api.validation import parse_student_name_path_param
from pipeline_runner import run_estimate_grade, run_estimate_rubrics

router = APIRouter()


def _estimate_or_http_error(result: dict) -> dict:
    """Turn estimate precondition failures into HTTP errors for consistent UI handling."""
    err = result.get("error")
    if err:
        raise HTTPException(status_code=400, detail=str(err))
    return result


@router.get("/estimate/rubrics")
def api_estimate_rubrics():
    """Estimate tokens and cost for rubric generation."""
    cfg = require_active_config()
    return _estimate_or_http_error(run_estimate_rubrics(cfg))


@router.get("/estimate/grade")
def api_estimate_grade_all():
    """Estimate tokens and cost for grading all students."""
    cfg = require_active_config()
    return _estimate_or_http_error(run_estimate_grade(cfg))


@router.get("/estimate/grade/{student_name:path}")
def api_estimate_grade_one(student_name: str):
    """Estimate tokens and cost for re-grading one student."""
    student_name = parse_student_name_path_param(student_name)
    cfg = require_active_config()
    return _estimate_or_http_error(run_estimate_grade(cfg, student_name=student_name))
