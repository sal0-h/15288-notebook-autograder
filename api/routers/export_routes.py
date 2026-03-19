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
    config = state.get_active_config()
    return await asyncio.to_thread(run_export, config)


@router.get("/export/excel")
def api_download_excel():
    """Download Final_Grades.xlsx."""
    config = state.get_active_config()
    output_dir = Path(config.get("output_dir", "output"))
    path = output_dir / "Final_Grades.xlsx"
    if not path.exists():
        raise HTTPException(
            status_code=404, detail="Excel not generated yet. Run export first."
        )
    return FileResponse(path, filename="Final_Grades.xlsx")


@router.get("/export/autograder-zip")
def api_download_autograder_zip():
    """Create Gradescope autograder zip and return it for download."""
    config = state.get_active_config()
    run_export(config)
    zip_path = run_export_autograder_zip(config)
    return FileResponse(zip_path, filename="gradescope_autograder.zip")


@router.get("/export/linter-zip")
def api_download_linter_zip():
    """Create linter autograder zip (pre-deadline)."""
    zip_path = export_linter_zip(state.get_active_config_path())
    return FileResponse(zip_path, filename="linter_autograder.zip")
