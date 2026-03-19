"""Bulk and single-student grading."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException

from batch_grader import grade_all_students
from api import sse as sse_mod
from api import state
from grade import grade_student
from llm.types import TokenUsage
from llm.usage_helpers import detach_usage_from_graded_result
from prompt_builder import validate_question_groups
from results_store import find_student, load_results, save_results, update_student
from utils import filter_groups_by_grade_only, get_assignment_output_paths

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/grade/status")
def api_grade_status():
    """Return whether grading is currently in progress."""
    acquired = state.grading_lock.acquire(blocking=False)
    if acquired:
        state.grading_lock.release()
    return {"in_progress": not acquired}


@router.get("/grade")
async def api_grade():
    """SSE stream: runs grading in a background thread, emits progress events."""
    if not state.grading_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Grading already in progress. Wait for it to finish or refresh.",
        )
    cfg = state.get_active_app_config()

    def worker(emit: Callable[[dict], None]) -> None:
        try:
            for evt in grade_all_students(cfg, results_lock=state.results_lock):
                emit(evt)
        except Exception as e:
            emit({"student": "", "status": "error", "result": None, "error": str(e)})

    def on_event(evt: dict) -> None:
        if evt.get("status") == "usage":
            u = TokenUsage.from_json_dict(evt.get("usage"))
            cost = evt.get("cost_usd", 0)
            logger.info(
                "Grading token usage: %s in / %s out — ~$%.4f",
                u.prompt_tokens,
                u.completion_tokens,
                cost,
            )

    return await sse_mod.threaded_sse_response(
        state.grading_lock, worker, on_event=on_event
    )


@router.post("/grade/{student_name:path}")
async def api_grade_one(student_name: str):
    """Re-grade a single student. Updates graded_results.json."""
    student_name = unquote(student_name)
    if "/" in student_name or "\\" in student_name or ".." in student_name:
        raise HTTPException(status_code=400, detail="Invalid student name")
    cfg = state.get_active_app_config()
    if not state.grading_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Bulk grading in progress. Wait for it to finish before re-grading one student.",
        )
    try:
        paths = get_assignment_output_paths(cfg)
        parsed_dir = paths.parsed_dir
        solution_path = paths.solution_parsed
        student_path = parsed_dir / f"{student_name}.json"
        if not solution_path.exists():
            raise HTTPException(status_code=404, detail="Run parse step first")
        if not student_path.exists():
            raise HTTPException(
                status_code=404, detail=f"Parsed notebook not found: {student_name}"
            )

        solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
        student_parsed = json.loads(student_path.read_text(encoding="utf-8"))
        student_parsed["student_name"] = student_name

        grading_config = cfg.grading
        groups = list(grading_config.question_groups)
        grade_only = grading_config.grade_only
        if grade_only:
            groups = filter_groups_by_grade_only(groups, grade_only)
        ungrouped = validate_question_groups(groups, solution_parsed)

        grade_only_merge = bool(grading_config.grade_only_merge and grade_only)
        merge_into = None
        out_path = paths.graded_results
        if grade_only_merge and out_path.exists():
            with state.results_lock:
                try:
                    existing_results = load_results(out_path)
                    merge_into = find_student(existing_results, student_name)
                except ValueError as e:
                    raise HTTPException(
                        status_code=500,
                        detail=str(e),
                    )

        result = await asyncio.to_thread(
            grade_student,
            student_parsed,
            solution_parsed,
            cfg,
            None,
            ungrouped,
            merge_into,
        )
        result_for_disk, usage = detach_usage_from_graded_result(result)

        with state.results_lock:
            try:
                results = load_results(out_path)
                update_student(results, student_name, result_for_disk)
                save_results(out_path, results)
            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))
        response = {"ok": True, "result": result_for_disk}
        if usage is not None and usage.has_tokens():
            response["usage"] = usage.to_json_dict()
        return response
    finally:
        state.grading_lock.release()
