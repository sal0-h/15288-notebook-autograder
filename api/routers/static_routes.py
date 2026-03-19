"""Root page and static UI assets."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from api.helpers import safe_path, ui_file_response
from api import state

router = APIRouter()


@router.get("/")
def root():
    """Serve the UI."""
    ui_path = state.PROJECT_ROOT / "ui" / "index.html"
    if ui_path.exists():
        return ui_file_response(ui_path)
    return {
        "message": "AI Autograder API. Open /ui/index.html or use the API endpoints."
    }


@router.get("/ui/{path:path}")
def serve_ui(path: str):
    """Serve static UI files."""
    ui_dir = state.PROJECT_ROOT / "ui"
    file_path = safe_path(ui_dir, path)
    if file_path.exists() and file_path.is_file():
        return ui_file_response(file_path)
    raise HTTPException(status_code=404)
