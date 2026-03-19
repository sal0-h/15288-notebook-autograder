"""Export downloads and batch export."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api import state
from linter_export import export_linter_zip
from pipeline_runner import run_export, run_export_autograder_zip

router = APIRouter()


@router.post("/export")
async def api_export():
    """Run export step. Returns summary and Excel download path."""
    cfg = state.get_active_app_config()
    return await asyncio.to_thread(run_export, cfg)


@router.get("/export/excel")
def api_download_excel():
    """Download Final_Grades.xlsx."""
    cfg = state.get_active_app_config()
    output_dir = Path(cfg.output_dir)
    path = output_dir / "Final_Grades.xlsx"
    if not path.exists():
        raise HTTPException(
            status_code=404, detail="Excel not generated yet. Run export first."
        )
    return FileResponse(path, filename="Final_Grades.xlsx")


@router.get("/export/autograder-zip")
def api_download_autograder_zip():
    """Create Gradescope autograder zip and return it for download."""
    cfg = state.get_active_app_config()
    run_export(cfg)
    zip_path = run_export_autograder_zip(cfg)
    return FileResponse(zip_path, filename="gradescope_autograder.zip")


@router.get("/export/linter-zip")
def api_download_linter_zip():
    """Create linter autograder zip (pre-deadline)."""
    zip_path = export_linter_zip(state.get_active_config_path())
    return FileResponse(zip_path, filename="linter_autograder.zip")
