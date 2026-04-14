"""Config, load/create, solution parse."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile

from api.helpers import build_parse_solution_response, resolve_solution_notebook_path
from api import state
from pydantic import ValidationError

from config_models import (
    AppConfig,
    default_config,
    ensure_app_config,
)
from parse_notebook import parse_notebook
from config_models import load_app_config, sanitize_assignment_name
from utils import save_config

router = APIRouter()


@router.get("/assignments")
def api_list_assignments():
    """List assignment folders under output/ that have a config.yaml."""
    out = state.PROJECT_ROOT / "output"
    names: list[str] = []
    if out.is_dir():
        for p in sorted(out.iterdir()):
            if p.is_dir() and (p / "config.yaml").is_file():
                names.append(p.name)
    return {"assignments": names}


@router.get("/config")
def api_get_config():
    """Return config for active assignment, or {} if none loaded."""
    if state.get_active_config_path() is None:
        return {}
    try:
        cfg = load_app_config(state.get_active_config_path())  # type: ignore[arg-type]
        return cfg.model_dump(mode="python")
    except (FileNotFoundError, ValueError, OSError) as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load active assignment config: {e}",
        )


@router.put("/config")
def api_put_config(config: dict = Body(...)):
    """Validate and update config.yaml from UI."""

    def _deep_merge(base: dict, override: dict) -> dict:
        merged = dict(base or {})
        for k, v in (override or {}).items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = _deep_merge(merged[k], v)
            else:
                merged[k] = v
        return merged

    try:
        existing = state.get_active_app_config().model_dump(mode="python")
    except HTTPException as e:
        if e.status_code == 400:
            existing = {}
        else:
            raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load active assignment config: {e}",
        )

    existing_assignment = str(existing.get("assignment_name", "")).strip()
    incoming_assignment = str(config.get("assignment_name", "")).strip()
    assignment_changed = bool(
        incoming_assignment
        and existing_assignment
        and incoming_assignment != existing_assignment
    )
    if assignment_changed and "solution_notebook" not in config:
        config = dict(config)
        config["solution_notebook"] = ""

    merged = _deep_merge(default_config("default"), _deep_merge(existing, config))
    try:
        validated = ensure_app_config(merged)
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=f"Invalid config: {e}")
    if state.get_active_config_path() is None:
        raise HTTPException(
            status_code=400,
            detail="No assignment loaded. Use Setup to load or create an assignment.",
        )
    # Plain dict for save_config / YAML I/O; already validated above.
    save_config(
        validated.model_dump(mode="python", exclude_none=True),
        state.get_active_config_path(),
    )  # type: ignore[arg-type]
    state.invalidate_config_cache()
    state.setup_file_logging()
    return {"ok": True}


@router.get("/config/default")
def api_get_config_default():
    """Return default config for new assignment setup."""
    return default_config("default")


@router.post("/load-or-create")
def api_load_or_create(body: dict = Body(...)):
    """Load assignment config or create folder + default config."""
    assignment_name = str(body.get("assignment_name", "")).strip()
    if not assignment_name:
        raise HTTPException(status_code=400, detail="assignment_name is required")
    safe_name = sanitize_assignment_name(assignment_name)
    if not safe_name or safe_name == "default":
        raise HTTPException(status_code=400, detail="Invalid assignment name")
    config_path = state.PROJECT_ROOT / "output" / safe_name / "config.yaml"
    was_created = not config_path.exists()
    if was_created:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        default = AppConfig(assignment_name=safe_name)
        save_config(default, config_path)
    state.set_active_config_path(config_path)
    cfg = load_app_config(config_path).model_dump(mode="python")
    state.setup_file_logging()
    return {"config": cfg, "created": was_created, "assignment_name": safe_name}


@router.post("/parse-solution-upload")
async def api_parse_solution_upload(
    assignment_name: str = Form(...),
    solution_file: UploadFile = File(...),
):
    """Upload solution notebook, save under output/{assignment}/, parse, return metadata."""
    if not assignment_name or not assignment_name.strip():
        raise HTTPException(status_code=400, detail="assignment_name is required")
    safe_name = sanitize_assignment_name(assignment_name.strip())
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid assignment name")
    if not solution_file.filename or not solution_file.filename.lower().endswith(
        ".ipynb"
    ):
        raise HTTPException(status_code=400, detail="Solution must be a .ipynb file")

    content = await solution_file.read()
    try:
        json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid notebook JSON")

    save_dir = state.PROJECT_ROOT / "output" / safe_name
    save_dir.mkdir(parents=True, exist_ok=True)
    solution_path = save_dir / f"{safe_name}_sol.ipynb"
    solution_path.write_bytes(content)

    config = (
        state.get_active_app_config().model_dump(mode="python")
        if state.get_active_config_path() is not None
        else default_config("default")
    )
    config["assignment_name"] = safe_name
    config["solution_notebook"] = str(solution_path.relative_to(state.PROJECT_ROOT))
    config["grading"] = config.get("grading", {})
    config["grading"]["question_groups"] = config["grading"].get("question_groups", [])

    parsed = parse_notebook(solution_path, ensure_app_config(config))
    return build_parse_solution_response(
        parsed,
        solution_notebook=config["solution_notebook"],
        assignment_name=safe_name,
    )


@router.post("/parse-solution")
async def api_parse_solution():
    """Parse the current solution notebook from config (no upload)."""
    config = state.get_active_app_config().model_dump(mode="python")
    solution_path = resolve_solution_notebook_path(
        state.PROJECT_ROOT, config.get("solution_notebook")
    )
    if solution_path is None:
        raise HTTPException(
            status_code=404,
            detail="Solution notebook path is not set. Upload one in Setup or set solution_notebook in config.",
        )
    if not solution_path.is_file():
        raise HTTPException(
            status_code=404, detail="Solution notebook not found. Upload one in Setup."
        )
    parsed = parse_notebook(solution_path, ensure_app_config(config))
    return build_parse_solution_response(parsed)
