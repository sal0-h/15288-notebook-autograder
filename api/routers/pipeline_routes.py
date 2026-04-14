"""Gather and parse pipeline steps."""

import asyncio
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from api import state
from pipeline_runner import run_gather, run_parse

router = APIRouter()

DEFAULT_UPLOAD_MB = 500


@router.post("/gather")
async def api_gather(zip_file: UploadFile = File(...)):
    """Run gather step. Expects a Gradescope export ZIP upload."""
    upload_max_mb = DEFAULT_UPLOAD_MB
    upload_max_bytes = upload_max_mb * 1024 * 1024

    tmp_path = None
    chunk_size = 1024 * 1024
    try:
        total_bytes = 0
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                chunk = await zip_file.read(chunk_size)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > upload_max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Upload exceeds {upload_max_mb}MB limit",
                    )
                tmp.write(chunk)

        try:
            with zipfile.ZipFile(tmp_path, "r"):
                pass
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP file")

        cfg = state.get_active_app_config()
        return run_gather(cfg, tmp_path)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


@router.post("/parse")
async def api_parse():
    """Run parse step. Returns verification report + first student preview."""
    cfg = state.get_active_app_config()
    return await asyncio.to_thread(run_parse, cfg)
