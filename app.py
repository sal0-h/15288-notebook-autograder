"""FastAPI backend for the AI Autograder pipeline."""

import asyncio
import json
import logging
import re
import tempfile
import threading
import zipfile
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from batch_grader import grade_all_students
from calibrate import run_calibration
from results_store import (
    find_student,
    load_results,
    save_results,
    update_student,
)
from estimate import estimate_grade, estimate_rubrics
from export import export_all, export_autograder_zip
from gather import gather_submissions
from grade import grade_student
from linter_export import export_linter_zip
from parse_notebook import (
    get_all_question_ids,
    parse_all_students,
    parse_notebook,
    sort_key_qid,
)
from prompt_builder import validate_question_groups
from rubric import generate_rubrics
from utils import (
    AppConfig,
    DEFAULT_MODEL,
    filter_groups_by_grade_only,
    load_config,
    sanitize_assignment_name,
    save_config,
    setup_assignment_logging,
)

logger = logging.getLogger(__name__)

# Active assignment config path — set by /load-or-create, drives all pipeline endpoints.
_active_config_path = None  # Path | None


def _get_active_config() -> dict:
    """Return the current assignment config. Raises 400 if no assignment is loaded."""
    if _active_config_path is None:
        raise HTTPException(
            status_code=400,
            detail="No assignment loaded. Use Setup to load or create an assignment.",
        )
    return load_config(_active_config_path)


def _setup_file_logging() -> None:
    """Configure the assignment-specific logger to write to the active assignment's autograder.log."""
    if _active_config_path is None:
        return
    try:
        cfg = load_config(_active_config_path)
        out_dir = Path(cfg.get("output_dir", "output"))
        assignment_name = cfg.get("assignment_name", "DEFAULT")
    except (FileNotFoundError, ValueError, OSError) as e:
        logger.warning(
            "Could not load config for file logging (%s): %s. Using defaults.",
            _active_config_path,
            e,
        )
        out_dir = Path("output")
        assignment_name = "DEFAULT"
    log_path = setup_assignment_logging(assignment_name, out_dir)
    logger.info("Logging to %s", log_path)


# Guard against concurrent grading runs (both write to graded_results.json)
_grading_lock = threading.Lock()
# Guard against concurrent reads/writes of graded_results.json (grading + review save)
_results_lock = threading.Lock()
_rubric_lock = threading.Lock()

DEFAULT_UPLOAD_MB = 500


def _safe_path(base: Path, user_input: str) -> Path:
    """Resolve path and ensure it stays under base. Raises HTTPException on path traversal."""
    base_resolved = base.resolve()
    resolved = (base / user_input).resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal detected")
    return resolved


def _build_parse_solution_response(
    parsed: dict,
    *,
    solution_notebook: str | None = None,
    assignment_name: str | None = None,
) -> dict:
    """Build the common response shape for parse-solution endpoints."""
    response = {
        "question_ids": get_all_question_ids(parsed),
        "sections": {
            k: list(v.get("questions", {}).keys())
            for k, v in parsed.get("sections", {}).items()
        },
        "duplicate_qids": parsed.get("duplicate_qids", []),
        "suggested_groups": _build_suggested_groups(parsed),
    }
    if solution_notebook is not None:
        response["solution_notebook"] = solution_notebook
    if assignment_name is not None:
        response["assignment_name"] = assignment_name
    return response


def _build_suggested_groups(parsed: dict) -> list[list[str]]:
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


def _error_event(error: str) -> dict:
    return {"status": "error", "error": error}


def _ui_file_response(path: Path) -> FileResponse:
    """Serve UI assets with no-store headers to avoid stale JS in active browser tabs."""
    return FileResponse(
        path,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


async def _threaded_sse_response(
    lock: threading.Lock,
    worker: Callable[[Callable[[dict], None]], None],
    on_event: Callable[[dict], None] | None = None,
) -> EventSourceResponse:
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def run_worker() -> None:
        try:
            worker(emit)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)
            lock.release()

    try:
        threading.Thread(target=run_worker, daemon=True).start()
    except Exception:
        lock.release()
        raise

    async def event_generator():
        try:
            while True:
                evt = await queue.get()
                if evt is None:
                    yield {"event": "done", "data": "{}"}
                    return
                if on_event is not None:
                    on_event(evt)
                yield {"event": "progress", "data": json.dumps(evt)}
        except GeneratorExit:
            pass

    return EventSourceResponse(event_generator())


@asynccontextmanager
async def _lifespan(app):
    _setup_file_logging()
    yield


app = FastAPI(title="AI Autograder", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------


@app.get("/config")
def api_get_config():
    """Return config for active assignment, or {} if none loaded."""
    if _active_config_path is None:
        return {}
    try:
        return load_config(_active_config_path)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load active assignment config: {e}",
        )


@app.put("/config")
def api_put_config(config: dict = Body(...)):
    """Validate and update config.yaml from UI.

    Accepts partial payloads from the setup UI by merging them into
    defaults + existing config before validating against AppConfig.
    """

    def _deep_merge(base: dict, override: dict) -> dict:
        merged = dict(base or {})
        for k, v in (override or {}).items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = _deep_merge(merged[k], v)
            else:
                merged[k] = v
        return merged

    try:
        existing = _get_active_config()
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

    merged = _deep_merge(_default_config(), _deep_merge(existing, config))
    try:
        AppConfig.model_validate(merged)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid config: {e}")
    if _active_config_path is None:
        raise HTTPException(
            status_code=400,
            detail="No assignment loaded. Use Setup to load or create an assignment.",
        )
    save_config(merged, _active_config_path)
    _setup_file_logging()
    return {"ok": True}


def _default_config() -> dict:
    """Return a minimal valid config for new assignments."""
    return {
        "assignment_name": "default",
        "model": DEFAULT_MODEL,
        "rubric_model": "",
        "rubric_review": True,
        "include_reference_in_grading": False,
        "solution_notebook": "",
        "output_dir": "output",
        "workers": 1,
        "max_prompt_tokens": 80_000,
        "max_completion_tokens": 4_096,
        "parsing": {
            "section_regex": r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
            "question_regex": r"(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
            "keep_images": True,
        },
        "grading": {
            "question_groups": [],
            "grade_only": None,
            "grade_only_merge": False,
        },
        "rubrics": {},
    }


@app.get("/config/default")
def api_get_config_default():
    """Return default config for new assignment setup."""
    return _default_config()


@app.post("/load-or-create")
def api_load_or_create(body: dict = Body(...)):
    """Load assignment config if it exists, or create a new folder with default config.

    Returns the config dict, whether the config was freshly created, and the safe assignment name.
    """
    global _active_config_path
    assignment_name = str(body.get("assignment_name", "")).strip()
    if not assignment_name:
        raise HTTPException(status_code=400, detail="assignment_name is required")
    safe_name = sanitize_assignment_name(assignment_name)
    if not safe_name or safe_name == "default":
        raise HTTPException(status_code=400, detail="Invalid assignment name")
    config_path = _PROJECT_ROOT / "output" / safe_name / "config.yaml"
    was_created = not config_path.exists()
    if was_created:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        default = AppConfig(assignment_name=safe_name)
        save_config(default, config_path)
    _active_config_path = config_path
    cfg = load_config(config_path)
    _setup_file_logging()
    return {"config": cfg, "created": was_created, "assignment_name": safe_name}


@app.post("/parse-solution-upload")
async def api_parse_solution_upload(
    assignment_name: str = Form(...),
    solution_file: UploadFile = File(...),
):
    """
    Upload solution notebook, save to {assignment_name}/{assignment_name}_sol.ipynb,
    parse it, and return question IDs, sections, duplicate warnings, and suggested groups.
    """
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

    save_dir = _PROJECT_ROOT / safe_name
    save_dir.mkdir(parents=True, exist_ok=True)
    solution_path = save_dir / f"{safe_name}_sol.ipynb"
    solution_path.write_bytes(content)

    config = dict(
        _get_active_config() if _active_config_path is not None else _default_config()
    )
    config["assignment_name"] = safe_name
    config["solution_notebook"] = str(solution_path.relative_to(_PROJECT_ROOT))
    config["grading"] = config.get("grading", {})
    config["grading"]["question_groups"] = config["grading"].get("question_groups", [])

    parsed = parse_notebook(solution_path, config)
    return _build_parse_solution_response(
        parsed,
        solution_notebook=config["solution_notebook"],
        assignment_name=safe_name,
    )


@app.post("/parse-solution")
async def api_parse_solution():
    """Parse the current solution notebook from config (no upload). Returns question IDs and suggested groups."""
    config = _get_active_config()
    solution_path = Path(config.get("solution_notebook", ""))
    if not solution_path or not solution_path.is_absolute():
        solution_path = _PROJECT_ROOT / config.get("solution_notebook", "")
    if not solution_path.exists():
        raise HTTPException(
            status_code=404, detail="Solution notebook not found. Upload one in Setup."
        )
    parsed = parse_notebook(solution_path, config)
    return _build_parse_solution_response(parsed)


# ---------------------------------------------------------------------------
# Pipeline endpoints
# ---------------------------------------------------------------------------


@app.post("/gather")
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

        config = _get_active_config()
        out_dir = Path(config.get("submissions_dir", "output/submissions"))
        results = gather_submissions(tmp_path, out_dir, from_zip=True)
        return {"results": results, "output_dir": str(out_dir)}
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


_PROJECT_ROOT = Path(__file__).resolve().parent


@app.post("/gather-from-folder")
def api_gather_from_folder(folder_path: str):
    """Run gather from an already-extracted folder path. Path must be under project root."""
    config = _get_active_config()
    out_dir = Path(config.get("submissions_dir", "output/submissions"))
    folder = (Path(folder_path)).resolve()
    if not folder.exists():
        raise HTTPException(status_code=400, detail=f"Folder not found: {folder_path}")
    try:
        folder.relative_to(_PROJECT_ROOT)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Folder path must be inside the project directory.",
        )
    results = gather_submissions(folder, out_dir, from_zip=False)
    return {"results": results, "output_dir": str(out_dir)}


@app.post("/parse")
async def api_parse():
    """Run parse step. Returns verification report + first student preview."""
    config = _get_active_config()
    solution_parsed, report = await asyncio.to_thread(parse_all_students, config)

    preview = None
    if report:
        first_student = report[0]["student_name"]
        parsed_dir = Path(config.get("parsed_dir", "output/parsed"))
        preview_path = parsed_dir / f"{first_student}.json"
        if preview_path.exists():
            preview = json.loads(preview_path.read_text(encoding="utf-8"))

    solution_questions = (
        get_all_question_ids(solution_parsed) if solution_parsed else []
    )
    solution_duplicate_qids = (
        solution_parsed.get("duplicate_qids", []) if solution_parsed else []
    )

    return {
        "report": report,
        "preview": preview,
        "solution_questions": solution_questions,
        "solution_duplicate_qids": solution_duplicate_qids,
    }


# ---------------------------------------------------------------------------
# Rubric endpoints
# ---------------------------------------------------------------------------


def _parse_group_indices_param(groups: str | None) -> list[int] | None:
    """Parse ?groups=0,2,4 into list of ints or None."""
    if not groups or not groups.strip():
        return None
    out = []
    for s in groups.split(","):
        s = s.strip()
        if s.isdigit():
            out.append(int(s))
    return out if out else None


@app.get("/generate-rubrics")
async def api_generate_rubrics_stream(
    groups: str | None = Query(
        None,
        description="Generate only for these group indices (0-based, e.g. 8 or 0,2,4). Merges into existing rubrics.",
    ),
):
    """SSE stream: generates rubrics from solution notebook, emits progress, saves to config when done."""
    if not _rubric_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Rubric generation already in progress. Wait for it to finish or refresh.",
        )
    config = _get_active_config()
    group_indices = _parse_group_indices_param(groups)

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
                config,
                progress_callback=progress_cb,
                group_indices=group_indices,
            )
            config["rubrics"] = rubrics
            save_config(config, _active_config_path)
            emit({"status": "done", "rubrics": rubrics})
        except Exception as e:
            emit(_error_event(str(e)))

    return await _threaded_sse_response(_rubric_lock, worker)


@app.post("/generate-rubrics")
async def api_generate_rubrics_post(
    body: dict | None = Body(None),
):
    """Generate rubrics (blocking). Saves to config. Use GET /generate-rubrics for progress stream.
    Body: {"group_indices": [8]} to generate only for those groups (merges, does not overwrite).
    """
    if not _rubric_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Rubric generation already in progress. Wait for it to finish or refresh.",
        )
    group_indices = None
    if body and body.get("group_indices") is not None:
        gi = body["group_indices"]
        group_indices = gi if isinstance(gi, list) else [int(gi)]
    try:
        config = _get_active_config()
        rubrics = await asyncio.to_thread(
            generate_rubrics, config, group_indices=group_indices
        )
        config["rubrics"] = rubrics
        save_config(config, _active_config_path)
        return {"rubrics": rubrics}
    finally:
        _rubric_lock.release()


@app.get("/rubrics")
def api_get_rubrics():
    """Read rubrics from config."""
    config = _get_active_config()
    return config.get("rubrics", {})


@app.put("/rubrics")
def api_put_rubrics(rubrics: dict = Body(...)):
    """Save edited rubrics to config."""
    config = _get_active_config()
    config["rubrics"] = rubrics
    try:
        AppConfig.model_validate(config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid rubrics: {e}")
    save_config(config, _active_config_path)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Estimate endpoints (cost/token preview before API calls)
# ---------------------------------------------------------------------------


@app.get("/estimate/rubrics")
def api_estimate_rubrics():
    """Estimate tokens and cost for rubric generation."""
    config = _get_active_config()
    return estimate_rubrics(config)


@app.get("/estimate/grade")
def api_estimate_grade_all():
    """Estimate tokens and cost for grading all students."""
    config = _get_active_config()
    return estimate_grade(config, student_name=None)


@app.get("/estimate/grade/{student_name:path}")
def api_estimate_grade_one(student_name: str):
    """Estimate tokens and cost for re-grading one student."""
    student_name = unquote(student_name)
    if "/" in student_name or "\\" in student_name or ".." in student_name:
        raise HTTPException(status_code=400, detail="Invalid student name")
    config = _get_active_config()
    return estimate_grade(config, student_name=student_name)


# ---------------------------------------------------------------------------
# Calibration endpoints
# ---------------------------------------------------------------------------


@app.post("/calibrate")
async def api_calibrate():
    """Run calibration (outlier detection) on graded results."""
    config = _get_active_config()
    flagged = await asyncio.to_thread(run_calibration, config)
    return {"flagged": flagged, "count": len(flagged)}


@app.get("/calibration")
def api_get_calibration():
    """Read saved calibration report."""
    config = _get_active_config()
    output_dir = Path(config.get("output_dir", "output"))
    path = output_dir / "calibration_report.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/grade/status")
def api_grade_status():
    """Return whether grading is currently in progress (for polling when SSE may have died)."""
    acquired = _grading_lock.acquire(blocking=False)
    if acquired:
        _grading_lock.release()
    return {"in_progress": not acquired}


@app.get("/grade")
async def api_grade():
    """SSE stream: runs grading in a background thread, emits progress events."""
    if not _grading_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Grading already in progress. Wait for it to finish or refresh.",
        )
    config = _get_active_config()

    def worker(emit: Callable[[dict], None]) -> None:
        try:
            for evt in grade_all_students(config, results_lock=_results_lock):
                emit(evt)
        except Exception as e:
            emit({"student": "", "status": "error", "result": None, "error": str(e)})

    def on_event(evt: dict) -> None:
        if evt.get("status") == "usage":
            u = evt.get("usage", {})
            cost = evt.get("cost_usd", 0)
            logger.info(
                "Grading token usage: %s in / %s out — ~$%.4f",
                u.get("prompt_tokens", 0),
                u.get("completion_tokens", 0),
                cost,
            )

    return await _threaded_sse_response(_grading_lock, worker, on_event=on_event)


@app.post("/grade/{student_name:path}")
async def api_grade_one(student_name: str):
    """Re-grade a single student. Updates graded_results.json."""
    student_name = unquote(student_name)
    if "/" in student_name or "\\" in student_name or ".." in student_name:
        raise HTTPException(status_code=400, detail="Invalid student name")
    config = _get_active_config()
    if not _grading_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Bulk grading in progress. Wait for it to finish before re-grading one student.",
        )
    try:
        output_dir = Path(config.get("output_dir", "output"))
        parsed_dir = Path(config.get("parsed_dir", "output/parsed"))
        solution_path = output_dir / "solution_parsed.json"
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

        grading_config = config.get("grading", {})
        groups = grading_config.get("question_groups", [])
        grade_only = grading_config.get("grade_only")
        if grade_only:
            groups = filter_groups_by_grade_only(groups, grade_only)
        ungrouped = validate_question_groups(groups, solution_parsed)

        grade_only_merge = bool(grading_config.get("grade_only_merge") and grade_only)
        merge_into = None
        out_path = output_dir / "graded_results.json"
        if grade_only_merge and out_path.exists():
            with _results_lock:
                try:
                    existing_results = load_results(out_path)
                    merge_into = find_student(existing_results, student_name)
                except ValueError as e:
                    raise HTTPException(
                        status_code=500,
                        detail=str(e),
                    )

        result = await asyncio.to_thread(
            grade_student,
            student_parsed,
            solution_parsed,
            config,
            None,
            ungrouped,
            merge_into,
        )
        usage = result.pop("_usage", None)

        with _results_lock:
            try:
                results = load_results(out_path)
                update_student(results, student_name, result)
                save_results(out_path, results)
            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))
        response = {"ok": True, "result": result}
        if usage:
            response["usage"] = usage
        return response
    finally:
        _grading_lock.release()


# ---------------------------------------------------------------------------
# Results endpoints
# ---------------------------------------------------------------------------


@app.get("/results")
def api_get_results():
    """Return full graded_results.json."""
    config = _get_active_config()
    output_dir = Path(config.get("output_dir", "output"))
    path = output_dir / "graded_results.json"
    with _results_lock:
        try:
            return load_results(path)
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.put("/results/{student_name:path}")
def api_put_results(student_name: str, result: dict = Body(...)):
    """Update one student's scores in graded_results.json."""
    student_name = unquote(student_name)
    if "/" in student_name or "\\" in student_name or ".." in student_name:
        raise HTTPException(status_code=400, detail="Invalid student name")
    required = ("student_name", "questions", "total_score", "total_max")
    if not all(k in result for k in required):
        raise HTTPException(
            status_code=422,
            detail=f"Missing required keys: {[k for k in required if k not in result]}",
        )
    config = _get_active_config()
    path = Path(config.get("output_dir", "output")) / "graded_results.json"

    if not path.exists():
        raise HTTPException(status_code=404, detail="No graded results yet")

    with _results_lock:
        try:
            results = load_results(path)
            update_student(results, student_name, result)
            save_results(path, results)
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Parsed notebook endpoint (for review UI)
# ---------------------------------------------------------------------------


@app.get("/parsed/{student_name:path}")
def api_get_parsed(student_name: str):
    """Return parsed JSON for a specific student."""
    student_name = unquote(student_name)
    config = _get_active_config()
    parsed_dir = Path(config.get("parsed_dir", "output/parsed"))
    path = _safe_path(parsed_dir, f"{student_name}.json")
    if not path.exists():
        raise HTTPException(
            status_code=404, detail=f"Parsed notebook not found for: {student_name}"
        )
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------


@app.post("/export")
async def api_export():
    """Run export step. Returns summary and Excel download path."""
    config = _get_active_config()
    summary = await asyncio.to_thread(export_all, config)
    return summary


@app.get("/export/excel")
def api_download_excel():
    """Download Final_Grades.xlsx."""
    config = _get_active_config()
    output_dir = Path(config.get("output_dir", "output"))
    path = output_dir / "Final_Grades.xlsx"
    if not path.exists():
        raise HTTPException(
            status_code=404, detail="Excel not generated yet. Run export first."
        )
    return FileResponse(path, filename="Final_Grades.xlsx")


@app.get("/export/autograder-zip")
def api_download_autograder_zip():
    """Create Gradescope autograder zip and return it for download."""
    config = _get_active_config()
    export_all(config)  # Ensure gradescope/*.json exist
    zip_path = export_autograder_zip(config)
    return FileResponse(zip_path, filename="gradescope_autograder.zip")


@app.get("/export/linter-zip")
def api_download_linter_zip():
    """Create linter autograder zip (Phase 1, pre-deadline). No grading required."""
    zip_path = export_linter_zip(_active_config_path)
    return FileResponse(zip_path, filename="linter_autograder.zip")


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    """Serve the UI."""
    ui_path = Path(__file__).parent / "ui" / "index.html"
    if ui_path.exists():
        return _ui_file_response(ui_path)
    return {
        "message": "AI Autograder API. Open /ui/index.html or use the API endpoints."
    }


@app.get("/ui/{path:path}")
def serve_ui(path: str):
    """Serve static UI files."""
    ui_dir = Path(__file__).parent / "ui"
    file_path = _safe_path(ui_dir, path)
    if file_path.exists() and file_path.is_file():
        return _ui_file_response(file_path)
    raise HTTPException(status_code=404)
