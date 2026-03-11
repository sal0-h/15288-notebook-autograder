"""FastAPI backend for the AI Autograder pipeline."""

import asyncio
import logging

logger = logging.getLogger(__name__)
import io
import json
import tempfile
import threading
import zipfile
from pathlib import Path
from urllib.parse import unquote

# Guard against concurrent grading runs (both write to graded_results.json)
_grading_lock = threading.Lock()
# Guard against concurrent reads/writes of graded_results.json (grading + review save)
_results_lock = threading.Lock()
_rubric_lock = threading.Lock()

DEFAULT_UPLOAD_MB = 500

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

import re

from utils import load_config, save_config, AppConfig, DEFAULT_MODEL
from gather import gather_submissions
from parse_notebook import parse_all_students, parse_notebook, get_all_question_ids
from grade import grade_all_students, grade_student, validate_question_groups
from export import export_all
from rubric import generate_rubrics
from calibrate import run_calibration
from estimate import estimate_rubrics, estimate_grade


def _safe_path(base: Path, user_input: str) -> Path:
    """Resolve path and ensure it stays under base. Raises HTTPException on path traversal."""
    base_resolved = base.resolve()
    resolved = (base / user_input).resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal detected")
    return resolved


app = FastAPI(title="AI Autograder")
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
    """Read current config.yaml."""
    path = Path("config.yaml")
    if not path.exists():
        return {}
    return load_config()


@app.put("/config")
def api_put_config(config: dict = Body(...)):
    """Validate and update config.yaml from UI."""
    try:
        AppConfig.model_validate(config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid config: {e}")
    save_config(config)
    return {"ok": True}


def _default_config() -> dict:
    """Return a minimal valid config for new assignments."""
    return {
        "assignment_name": "default",
        "model": DEFAULT_MODEL,
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
        "grading": {"question_groups": []},
        "prompts": {
            "system": "You are an expert Python instructor grading student lab work.\n\nReturn valid JSON only.",
        },
        "rubrics": {},
    }


@app.get("/config/default")
def api_get_config_default():
    """Return default config for new assignment setup."""
    return _default_config()


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
    safe_name = re.sub(r'[/\\:*?"<>|]', "_", assignment_name.strip()).strip("_")
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid assignment name")
    if not solution_file.filename or not solution_file.filename.lower().endswith(".ipynb"):
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

    config = load_config() if Path("config.yaml").exists() else _default_config()
    config["assignment_name"] = safe_name
    config["solution_notebook"] = str(solution_path.relative_to(_PROJECT_ROOT))
    config["grading"] = config.get("grading", {})
    config["grading"]["question_groups"] = config["grading"].get("question_groups", [])

    parsed = parse_notebook(solution_path, config)
    question_ids = get_all_question_ids(parsed)
    duplicate_qids = parsed.get("duplicate_qids", [])

    suggested_groups: list[list[str]] = []
    for sec_id in sorted(parsed.get("sections", {}).keys(), key=lambda s: (int(s) if s.isdigit() else 999, s)):
        sec_data = parsed["sections"][sec_id]
        qids = sorted(sec_data.get("questions", {}).keys(), key=lambda q: (
            int(q.split(".")[0]) if "." in q else 999,
            int(q.split(".")[1]) if "." in q and q.split(".")[1].isdigit() else 0,
        ))
        if qids:
            suggested_groups.append(qids)

    return {
        "question_ids": question_ids,
        "sections": {k: list(v.get("questions", {}).keys()) for k, v in parsed.get("sections", {}).items()},
        "duplicate_qids": duplicate_qids,
        "suggested_groups": suggested_groups,
        "solution_notebook": config["solution_notebook"],
        "assignment_name": safe_name,
    }


@app.post("/parse-solution")
async def api_parse_solution():
    """Parse the current solution notebook from config (no upload). Returns question IDs and suggested groups."""
    config = load_config()
    solution_path = Path(config.get("solution_notebook", ""))
    if not solution_path or not solution_path.is_absolute():
        solution_path = _PROJECT_ROOT / config.get("solution_notebook", "")
    if not solution_path.exists():
        raise HTTPException(status_code=404, detail="Solution notebook not found. Upload one in Setup.")
    parsed = parse_notebook(solution_path, config)
    question_ids = get_all_question_ids(parsed)
    duplicate_qids = parsed.get("duplicate_qids", [])
    suggested_groups: list[list[str]] = []
    for sec_id in sorted(parsed.get("sections", {}).keys(), key=lambda s: (int(s) if s.isdigit() else 999, s)):
        sec_data = parsed["sections"][sec_id]
        qids = sorted(sec_data.get("questions", {}).keys(), key=lambda q: (
            int(q.split(".")[0]) if "." in q else 999,
            int(q.split(".")[1]) if "." in q and q.split(".")[1].isdigit() else 0,
        ))
        if qids:
            suggested_groups.append(qids)
    return {
        "question_ids": question_ids,
        "sections": {k: list(v.get("questions", {}).keys()) for k, v in parsed.get("sections", {}).items()},
        "duplicate_qids": duplicate_qids,
        "suggested_groups": suggested_groups,
    }


# ---------------------------------------------------------------------------
# Pipeline endpoints
# ---------------------------------------------------------------------------

@app.post("/gather")
async def api_gather(zip_file: UploadFile = File(...)):
    """Run gather step. Expects a Gradescope export ZIP upload."""
    config = load_config()
    out_dir = Path(config.get("submissions_dir", "output/submissions"))
    upload_max_mb = config.get("upload_max_mb", DEFAULT_UPLOAD_MB)
    upload_max_bytes = upload_max_mb * 1024 * 1024

    content = await zip_file.read()
    if len(content) > upload_max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Upload exceeds {upload_max_mb}MB limit",
        )
    try:
        zipfile.ZipFile(io.BytesIO(content), "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP file")

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        results = gather_submissions(tmp_path, out_dir, from_zip=True)
        return {"results": results, "output_dir": str(out_dir)}
    finally:
        tmp_path.unlink(missing_ok=True)


_PROJECT_ROOT = Path(__file__).resolve().parent


@app.post("/gather-from-folder")
def api_gather_from_folder(folder_path: str):
    """Run gather from an already-extracted folder path. Path must be under project root."""
    config = load_config()
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
    config = load_config()
    solution_parsed, report = await asyncio.to_thread(parse_all_students, config)

    preview = None
    if report:
        first_student = report[0]["student_name"]
        parsed_dir = Path(config.get("parsed_dir", "output/parsed"))
        preview_path = parsed_dir / f"{first_student}.json"
        if preview_path.exists():
            preview = json.loads(preview_path.read_text(encoding="utf-8"))

    solution_questions = get_all_question_ids(solution_parsed) if solution_parsed else []
    solution_duplicate_qids = solution_parsed.get("duplicate_qids", []) if solution_parsed else []

    return {
        "report": report,
        "preview": preview,
        "solution_questions": solution_questions,
        "solution_duplicate_qids": solution_duplicate_qids,
    }


# ---------------------------------------------------------------------------
# Rubric endpoints
# ---------------------------------------------------------------------------

@app.get("/generate-rubrics")
async def api_generate_rubrics_stream():
    """SSE stream: generates rubrics from solution notebook, emits progress, saves to config when done."""
    if not _rubric_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Rubric generation already in progress. Wait for it to finish or refresh.",
        )
    thread_started = False
    try:
        config = load_config()
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _rubric_thread():
            try:
                def progress_cb(idx: int, total: int, group: list, rubrics_so_far: dict):
                    # If any qid from this group is in rubrics_so_far, group is done; else starting
                    is_done = any(q in rubrics_so_far for q in group)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"status": "progress", "current": idx, "total": total, "group": group, "done": is_done},
                    )

                rubrics = generate_rubrics(config, progress_callback=progress_cb)
                config["rubrics"] = rubrics
                save_config(config)
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"status": "done", "rubrics": rubrics},
                )
            except Exception as e:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"status": "error", "error": str(e)},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)
                _rubric_lock.release()

        threading.Thread(target=_rubric_thread, daemon=True).start()
        thread_started = True

        async def event_generator():
            try:
                while True:
                    evt = await queue.get()
                    if evt is None:
                        yield {"event": "done", "data": "{}"}
                        return
                    yield {"event": "progress", "data": json.dumps(evt)}
            except GeneratorExit:
                pass

        return EventSourceResponse(event_generator())
    except Exception:
        if not thread_started:
            _rubric_lock.release()
        raise


@app.post("/generate-rubrics")
async def api_generate_rubrics_post():
    """Generate rubrics (blocking). Saves to config. Use GET /generate-rubrics for progress stream."""
    if not _rubric_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Rubric generation already in progress. Wait for it to finish or refresh.",
        )
    try:
        config = load_config()
        rubrics = await asyncio.to_thread(generate_rubrics, config)
        config["rubrics"] = rubrics
        save_config(config)
        return {"rubrics": rubrics}
    finally:
        _rubric_lock.release()


@app.get("/rubrics")
def api_get_rubrics():
    """Read rubrics from config."""
    config = load_config()
    return config.get("rubrics", {})


@app.put("/rubrics")
def api_put_rubrics(rubrics: dict = Body(...)):
    """Save edited rubrics to config."""
    config = load_config()
    config["rubrics"] = rubrics
    try:
        AppConfig.model_validate(config)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid rubrics: {e}")
    save_config(config)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Estimate endpoints (cost/token preview before API calls)
# ---------------------------------------------------------------------------

@app.get("/estimate/rubrics")
def api_estimate_rubrics():
    """Estimate tokens and cost for rubric generation."""
    config = load_config()
    return estimate_rubrics(config)


@app.get("/estimate/grade")
def api_estimate_grade_all():
    """Estimate tokens and cost for grading all students."""
    config = load_config()
    return estimate_grade(config, student_name=None)


@app.get("/estimate/grade/{student_name:path}")
def api_estimate_grade_one(student_name: str):
    """Estimate tokens and cost for re-grading one student."""
    student_name = unquote(student_name)
    if "/" in student_name or "\\" in student_name or ".." in student_name:
        raise HTTPException(status_code=400, detail="Invalid student name")
    config = load_config()
    return estimate_grade(config, student_name=student_name)


# ---------------------------------------------------------------------------
# Calibration endpoints
# ---------------------------------------------------------------------------

@app.post("/calibrate")
async def api_calibrate():
    """Run calibration (outlier detection) on graded results."""
    config = load_config()
    flagged = await asyncio.to_thread(run_calibration, config)
    return {"flagged": flagged, "count": len(flagged)}


@app.get("/calibration")
def api_get_calibration():
    """Read saved calibration report."""
    config = load_config()
    output_dir = Path(config.get("output_dir", "output"))
    path = output_dir / "calibration_report.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/grade")
async def api_grade():
    """SSE stream: runs grading in a background thread, emits progress events."""
    if not _grading_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Grading already in progress. Wait for it to finish or refresh.",
        )
    thread_started = False
    try:
        config = load_config()
        queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _grade_thread():
            try:
                for evt in grade_all_students(config, results_lock=_results_lock):
                    loop.call_soon_threadsafe(queue.put_nowait, evt)
            except Exception as e:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"student": "", "status": "error", "result": None, "error": str(e)},
                )
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)
                _grading_lock.release()

        threading.Thread(target=_grade_thread, daemon=True).start()
        thread_started = True

        async def event_generator():
            try:
                while True:
                    evt = await queue.get()
                    if evt is None:
                        yield {"event": "done", "data": "{}"}
                        return
                    if evt.get("status") == "usage":
                        u = evt.get("usage", {})
                        cost = evt.get("cost_usd", 0)
                        logger.info(
                            "Grading token usage: %s in / %s out — ~$%.4f",
                            u.get("prompt_tokens", 0),
                            u.get("completion_tokens", 0),
                            cost,
                        )
                    yield {"event": "progress", "data": json.dumps(evt)}
            except GeneratorExit:
                pass  # client disconnected; lock released in thread

        return EventSourceResponse(event_generator())
    except Exception:
        if not thread_started:
            _grading_lock.release()
        raise


@app.post("/grade/{student_name:path}")
async def api_grade_one(student_name: str):
    """Re-grade a single student. Updates graded_results.json."""
    student_name = unquote(student_name)
    if "/" in student_name or "\\" in student_name or ".." in student_name:
        raise HTTPException(status_code=400, detail="Invalid student name")
    config = load_config()
    output_dir = Path(config.get("output_dir", "output"))
    parsed_dir = Path(config.get("parsed_dir", "output/parsed"))
    solution_path = output_dir / "solution_parsed.json"
    student_path = parsed_dir / f"{student_name}.json"
    if not solution_path.exists():
        raise HTTPException(status_code=404, detail="Run parse step first")
    if not student_path.exists():
        raise HTTPException(status_code=404, detail=f"Parsed notebook not found: {student_name}")

    solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
    student_parsed = json.loads(student_path.read_text(encoding="utf-8"))
    student_parsed["student_name"] = student_name

    grading_config = config.get("grading", {})
    groups = grading_config.get("question_groups", [])
    grade_only = grading_config.get("grade_only")
    if grade_only:
        groups = [[q for q in g if q in set(grade_only)] for g in groups]
        groups = [g for g in groups if g]
    ungrouped = validate_question_groups(groups, solution_parsed)

    result = await asyncio.to_thread(
        grade_student, student_parsed, solution_parsed, config, None, ungrouped
    )

    out_path = output_dir / "graded_results.json"
    with _results_lock:
        raw = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else []
        results = raw if isinstance(raw, list) else []
        found = False
        for i, r in enumerate(results):
            if isinstance(r, dict) and r.get("student_name") == student_name:
                results[i] = result
                found = True
                break
        if not found:
            results.append(result)
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return {"ok": True, "result": result}


# ---------------------------------------------------------------------------
# Results endpoints
# ---------------------------------------------------------------------------

@app.get("/results")
def api_get_results():
    """Return full graded_results.json."""
    config = load_config()
    output_dir = Path(config.get("output_dir", "output"))
    path = output_dir / "graded_results.json"
    if not path.exists():
        return []
    with _results_lock:
        return json.loads(path.read_text(encoding="utf-8"))


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
    config = load_config()
    output_dir = Path(config.get("output_dir", "output"))
    path = output_dir / "graded_results.json"

    if not path.exists():
        raise HTTPException(status_code=404, detail="No graded results yet")

    with _results_lock:
        results = json.loads(path.read_text(encoding="utf-8"))
        found = False
        for i, r in enumerate(results):
            if r.get("student_name") == student_name:
                results[i] = result
                found = True
                break
        if not found:
            results.append(result)
        path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Parsed notebook endpoint (for review UI)
# ---------------------------------------------------------------------------

@app.get("/parsed/{student_name:path}")
def api_get_parsed(student_name: str):
    """Return parsed JSON for a specific student."""
    student_name = unquote(student_name)
    config = load_config()
    parsed_dir = Path(config.get("parsed_dir", "output/parsed"))
    path = _safe_path(parsed_dir, f"{student_name}.json")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Parsed notebook not found for: {student_name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------

@app.post("/export")
async def api_export():
    """Run export step. Returns summary and Excel download path."""
    config = load_config()
    summary = await asyncio.to_thread(export_all, config)
    return summary


@app.get("/export/excel")
def api_download_excel():
    """Download Final_Grades.xlsx."""
    config = load_config()
    output_dir = Path(config.get("output_dir", "output"))
    path = output_dir / "Final_Grades.xlsx"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Excel not generated yet. Run export first.")
    return FileResponse(path, filename="Final_Grades.xlsx")


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    """Serve the UI."""
    ui_path = Path(__file__).parent / "ui" / "index.html"
    if ui_path.exists():
        return FileResponse(ui_path)
    return {"message": "AI Autograder API. Open /ui/index.html or use the API endpoints."}


@app.get("/ui/{path:path}")
def serve_ui(path: str):
    """Serve static UI files."""
    ui_dir = Path(__file__).parent / "ui"
    file_path = _safe_path(ui_dir, path)
    if file_path.exists() and file_path.is_file():
        return FileResponse(file_path)
    raise HTTPException(status_code=404)
