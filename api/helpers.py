"""HTTP helpers shared by API routers.

**Errors:** FastAPI routes should signal failures with ``HTTPException`` and
appropriate status codes. Estimate routes map precondition failures from
``estimate.py`` to **HTTP 400** with a string ``detail`` (the UI also understands
legacy ``error`` in JSON for direct ``estimate_*`` Python callers).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from config_models import AppConfig, sort_key_qid
from parse_notebook import get_all_question_ids


def require_active_config() -> AppConfig:
    """Load the active assignment config or raise an appropriate HTTPException.

    Centralises the config-loading try/except pattern used by most routers.
    """
    from api import state

    try:
        return state.get_active_app_config()
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Config file not found.")
    except (ValueError, OSError) as e:
        raise HTTPException(status_code=500, detail=f"Failed to load config: {e}")


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Returns new dict."""
    merged = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def safe_path(base: Path, user_input: str) -> Path:
    """Resolve path and ensure it stays under base. Raises HTTPException on path traversal."""
    base_resolved = base.resolve()
    resolved = (base / user_input).resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal detected")
    return resolved


def resolve_solution_notebook_path(
    project_root: Path, solution_notebook: str | None
) -> Path | None:
    """Absolute path to the solution ``.ipynb``, or ``None`` if unset/blank.

    Relative paths are resolved under ``project_root`` with :func:`safe_path`
    (rejects ``..`` traversal). Empty string must not resolve to ``project_root`` alone.
    """
    raw = (solution_notebook or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p
    return safe_path(project_root, raw)


def ui_file_response(path: Path) -> FileResponse:
    """Serve UI assets with no-store headers to avoid stale JS in active browser tabs."""
    return FileResponse(
        path,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


def error_event(error: str) -> dict:
    return {"status": "error", "error": error}


def build_suggested_groups(parsed: dict) -> list[list[str]]:
    suggested_groups: list[list[str]] = []
    for sec_id in sorted(
        parsed.get("sections", {}).keys(),
        key=lambda s: (int(s) if s.isdigit() else 999, s),
    ):
        sec_data = parsed["sections"][sec_id]
        qids = sorted(sec_data.get("questions", {}).keys(), key=sort_key_qid)
        if qids:
            suggested_groups.append(qids)
    return suggested_groups


def build_parse_solution_response(
    parsed: dict,
    *,
    solution_notebook: str | None = None,
    assignment_name: str | None = None,
) -> dict:
    """Common response shape for parse-solution endpoints."""
    response = {
        "question_ids": get_all_question_ids(parsed),
        "sections": {
            k: list(v.get("questions", {}).keys())
            for k, v in parsed.get("sections", {}).items()
        },
        "duplicate_qids": parsed.get("duplicate_qids", []),
        "suggested_groups": build_suggested_groups(parsed),
    }
    if solution_notebook is not None:
        response["solution_notebook"] = solution_notebook
    if assignment_name is not None:
        response["assignment_name"] = assignment_name
    return response


def parse_group_indices_param(groups: str | None) -> list[int] | None:
    """Parse ?groups=0,2,4 into list of ints or None."""
    if not groups or not groups.strip():
        return None
    out = []
    for s in groups.split(","):
        s = s.strip()
        if s.isdigit():
            out.append(int(s))
    return out if out else None
