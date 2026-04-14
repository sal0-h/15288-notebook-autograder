"""Bulk and single-student grading."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from pathlib import Path
from fastapi import APIRouter, HTTPException

from batch_grader import grade_all_students
from api import sse as sse_mod
from api import state
from api.validation import parse_student_name_path_param
from genai_detection import run_genai_detection
from grading_helpers import effective_groups
from grade import grade_student
from results_models import (
    GradedResult,
    TokenUsage,
    detach_usage_from_graded_result,
)
from prompt_builder import validate_question_groups
from results_store import find_student, load_results, save_results, update_student
from utils import get_assignment_output_paths

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
    student_name = parse_student_name_path_param(student_name)
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
        groups = effective_groups(grading_config)
        ungrouped = validate_question_groups(groups, solution_parsed)

        grade_only_merge = grading_config.grade_only_merge
        merge_into = None
        out_path = paths.graded_results
        if grade_only_merge and out_path.exists():
            with state.results_lock:
                try:
                    existing_results = load_results(out_path)
                    found = find_student(existing_results, student_name)
                    merge_into = (
                        found.model_dump(mode="python") if found is not None else None
                    )
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
                update_student(
                    results,
                    student_name,
                    GradedResult.model_validate(result_for_disk),
                )
                save_results(out_path, results)
            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))
        response = {"ok": True, "result": result_for_disk}
        if usage is not None and usage.has_tokens():
            response["usage"] = usage.to_json_dict()
        return response
    finally:
        state.grading_lock.release()


@router.post("/detect-genai")
async def api_detect_genai():
    """Optional second pass: merge GenAI suspicion flags into graded_results.json."""
    if not state.grading_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Grading in progress. Wait before running GenAI detection.",
        )
    try:
        cfg = state.get_active_app_config()

        def _run():
            return run_genai_detection(cfg)

        with state.results_lock:
            summary = await asyncio.to_thread(_run)
        return summary
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    finally:
        state.grading_lock.release()
