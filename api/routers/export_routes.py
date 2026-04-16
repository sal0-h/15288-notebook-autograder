"""Export downloads and batch export."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api import state
from api.helpers import require_active_config
from linter_export import export_linter_zip
from export import export_all, export_autograder_zip

router = APIRouter()


@router.post("/export")
async def api_export():
    """Run export step. Returns summary and Excel download path."""
    cfg = require_active_config()
    return await asyncio.to_thread(export_all, cfg)


@router.get("/export/excel")
def api_download_excel():
    """Download Final_Grades.xlsx."""
    cfg = require_active_config()
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
    cfg = require_active_config()
    zip_path = export_autograder_zip(cfg)
    return FileResponse(zip_path, filename="gradescope_autograder.zip")


@router.get("/export/linter-zip")
def api_download_linter_zip():
    """Create linter autograder zip (pre-deadline)."""
    zip_path = export_linter_zip(state.get_active_config_path())
    return FileResponse(zip_path, filename="linter_autograder.zip")
