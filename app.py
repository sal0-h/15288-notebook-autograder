"""FastAPI backend for the AI Autograder pipeline."""

import asyncio
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

MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500MB

from fastapi import FastAPI, File, UploadFile, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from utils import load_config, save_config, AppConfig
from gather import gather_submissions
from parse_notebook import parse_all_students, get_all_question_ids
from grade import grade_all_students
from export import export_all
from rubric import generate_rubrics
from calibrate import run_calibration


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


# ---------------------------------------------------------------------------
# Pipeline endpoints
# ---------------------------------------------------------------------------

@app.post("/gather")
async def api_gather(zip_file: UploadFile = File(...)):
    """Run gather step. Expects a Gradescope export ZIP upload."""
    config = load_config()
    out_dir = Path(config.get("submissions_dir", "output/submissions"))

    content = await zip_file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)}MB limit",
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

    return {
        "report": report,
        "preview": preview,
        "solution_questions": solution_questions,
    }


# ---------------------------------------------------------------------------
# Rubric endpoints
# ---------------------------------------------------------------------------

@app.post("/generate-rubrics")
async def api_generate_rubrics():
    """Generate rubrics from solution notebook. Saves to config."""
    config = load_config()
    rubrics = await asyncio.to_thread(generate_rubrics, config)
    config["rubrics"] = rubrics
    save_config(config)
    return {"rubrics": rubrics}


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
                    yield {"event": "progress", "data": json.dumps(evt)}
            except GeneratorExit:
                pass  # client disconnected; lock released in thread

        return EventSourceResponse(event_generator())
    except Exception:
        if not thread_started:
            _grading_lock.release()
        raise


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
