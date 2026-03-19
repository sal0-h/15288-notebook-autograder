"""FastAPI backend for the AI Autograder pipeline."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import state
from api.constants import DEFAULT_UPLOAD_MB  # noqa: F401 — re-exported for tests
from api.routers import (
    calibrate_routes,
    config_routes,
    estimate_routes,
    export_routes,
    grade_routes,
    pipeline_routes,
    results_routes,
    rubric_routes,
    static_routes,
)
# Backward compatibility for tests that patch ``app.*`` (monolithic app surface).
_get_active_config = state.get_active_config
_setup_file_logging = state.setup_file_logging
_PROJECT_ROOT = state.PROJECT_ROOT
_grading_lock = state.grading_lock
_results_lock = state.results_lock
_rubric_lock = state.rubric_lock


@asynccontextmanager
async def _lifespan(app):
    state.setup_file_logging()
    yield


app = FastAPI(title="AI Autograder", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (
    config_routes.router,
    pipeline_routes.router,
    rubric_routes.router,
    estimate_routes.router,
    calibrate_routes.router,
    grade_routes.router,
    results_routes.router,
    export_routes.router,
    static_routes.router,
):
    app.include_router(r)
