"""Calibration / outlier report."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from api import state
from api.helpers import require_active_config
from pipeline_runner import get_calibration_report, run_calibrate_step

router = APIRouter()


@router.post("/calibrate")
async def api_calibrate():
    """Run calibration (outlier detection) on graded results."""
    cfg = require_active_config()
    flagged = await asyncio.to_thread(run_calibrate_step, cfg)
    return {"flagged": flagged, "count": len(flagged)}


@router.get("/calibration")
def api_get_calibration():
    """Read saved calibration report."""
    cfg = require_active_config()
    return get_calibration_report(cfg)
