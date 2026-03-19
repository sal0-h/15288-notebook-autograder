"""Rubric generation and editing."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, Body, HTTPException, Query

from api.helpers import error_event, parse_group_indices_param
from api import sse as sse_mod
from api import state
from pydantic import ValidationError

from config_models import ensure_app_config
from rubric import generate_rubrics
from utils import save_config

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/generate-rubrics")
async def api_generate_rubrics_stream(
    groups: str | None = Query(
        None,
        description="Generate only for these group indices (0-based, e.g. 8 or 0,2,4). Merges into existing rubrics.",
    ),
):
    """SSE stream: generates rubrics, emits progress, saves to config when done."""
    if not state.rubric_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Rubric generation already in progress. Wait for it to finish or refresh.",
        )
    cfg = state.get_active_app_config()
    group_indices = parse_group_indices_param(groups)

    def worker(emit: Callable[[dict], None]) -> None:
        try:

            def progress_cb(idx: int, total: int, group: list, rubrics_so_far: dict):
                is_done = any(q in rubrics_so_far for q in group)
                emit(
                    {
                        "status": "progress",
                        "current": idx,
                        "total": total,
                        "group": group,
                        "done": is_done,
                    }
                )

            rubrics = generate_rubrics(
                cfg,
                progress_callback=progress_cb,
                group_indices=group_indices,
            )
            data = cfg.model_dump(mode="python")
            data["rubrics"] = rubrics
            save_config(data, state.get_active_config_path())  # type: ignore[arg-type]
            emit({"status": "done", "rubrics": rubrics})
        except Exception as e:
            emit(error_event(str(e)))

    return await sse_mod.threaded_sse_response(state.rubric_lock, worker)


@router.post("/generate-rubrics")
async def api_generate_rubrics_post(
    body: dict | None = Body(None),
):
    """Generate rubrics (blocking). Saves to config."""
    if not state.rubric_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Rubric generation already in progress. Wait for it to finish or refresh.",
        )
    group_indices = None
    if body and body.get("group_indices") is not None:
        gi = body["group_indices"]
        group_indices = gi if isinstance(gi, list) else [int(gi)]
    try:
        cfg = state.get_active_app_config()
        rubrics = await asyncio.to_thread(
            generate_rubrics, cfg, group_indices=group_indices
        )
        data = cfg.model_dump(mode="python")
        data["rubrics"] = rubrics
        save_config(data, state.get_active_config_path())  # type: ignore[arg-type]
        return {"rubrics": rubrics}
    finally:
        state.rubric_lock.release()


@router.get("/rubrics")
def api_get_rubrics():
    """Read rubrics from config."""
    config = state.get_active_config()
    return config.get("rubrics", {})


@router.put("/rubrics")
def api_put_rubrics(rubrics: dict = Body(...)):
    """Save edited rubrics to config."""
    merged = dict(state.get_active_config())
    merged["rubrics"] = rubrics
    try:
        ensure_app_config(merged)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid rubrics: {e}")
    save_config(merged, state.get_active_config_path())  # type: ignore[arg-type]
    return {"ok": True}
