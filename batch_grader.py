"""Batch grading orchestration — sequential and parallel, with resume support."""

import json
import logging
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Generator

from openai import OpenAI

from grading_models import MODEL_PRICING
from prompt_builder import validate_question_groups
from utils import (
    AppConfig,
    ensure_app_config,
    DEFAULT_MODEL,
    get_active_grade_only,
    get_effective_question_groups,
    get_openai_client,
    is_grade_only_merge_enabled,
    needs_grade_only_merge,
    get_job_logger,
)

logger = logging.getLogger(__name__)


def grade_all_students(
    config: AppConfig | dict,
    client: OpenAI | None = None,
    results_lock=None,
) -> Generator[dict, None, None]:
    """
    Grade all students sequentially or in parallel.
    Yields progress events; saves graded_results.json after each student.
    When results_lock is provided (e.g. from app), uses it for thread-safe writes.
    """
    logger = get_job_logger(config, __name__)
    cfg = ensure_app_config(config)

    # Import here to avoid circular dependency (grade imports batch_grader for main())
    from grade import grade_student  # noqa: PLC0415

    if client is None:
        pool_size = max(1, int(cfg.workers))
        client = get_openai_client(
            max_connections=pool_size,
            max_keepalive_connections=pool_size,
            read_timeout_s=120.0,
        )

    output_dir = Path(cfg.output_dir)
    parsed_dir = Path(cfg.parsed_dir)
    solution_path = output_dir / "solution_parsed.json"

    if not solution_path.exists():
        raise FileNotFoundError(
            f"Solution parsed not found: {solution_path}. Run the parse step first."
        )

    solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
    student_files = sorted(parsed_dir.glob("*.json"))
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "graded_results.json"

    def _read_results():
        if out_path.exists():
            try:
                return json.loads(out_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, KeyError):
                backup_path = output_dir / "graded_results.json.broken"
                try:
                    shutil.copy2(out_path, backup_path)
                    logger.error(
                        "graded_results.json is corrupted (invalid JSON). "
                        "Backed up to %s. Starting fresh — previous grades will be re-run.",
                        backup_path,
                        exc_info=True,
                    )
                    print(
                        f"Error: graded_results.json is corrupted. Backed up to {backup_path}. Re-grading all students."
                    )
                except OSError:
                    logger.exception("Failed to backup corrupted graded_results.json")
                return []
        return []

    def _write_results(data):
        out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _store_result(student_name: str, result: dict) -> None:
        """Update results list/index and persist — call with lock held if parallel."""
        if student_name in results_by_name:
            results[results_by_name[student_name]] = result
        else:
            results.append(result)
            results_by_name[student_name] = len(results) - 1
        _write_results(results)

    # Load any previously saved results for resume support
    if results_lock:
        with results_lock:
            raw = _read_results()
    else:
        raw = _read_results()

    grading_config = cfg.grading

    if isinstance(raw, list):
        results: list[dict] = raw
        already_graded = {
            r["student_name"]
            for r in raw
            if isinstance(r, dict) and "student_name" in r
        }
        results_by_name = {
            r["student_name"]: idx
            for idx, r in enumerate(raw)
            if isinstance(r, dict) and "student_name" in r
        }
    else:
        results = []
        already_graded = set()
        results_by_name = {}

    grade_only_merge = is_grade_only_merge_enabled(grading_config)
    grade_only = get_active_grade_only(grading_config)

    if grade_only_merge:
        to_grade = [
            (i, path)
            for i, path in enumerate(student_files)
            if needs_grade_only_merge(
                (
                    results[results_by_name[path.stem]]
                    if path.stem in results_by_name
                    else None
                ),
                grade_only,
            )
        ]
        skipped = len(student_files) - len(to_grade)
        print(
            f"grade_only_merge: grading {len(to_grade)} students (skipping {skipped} already graded for grade_only)"
        )
    else:
        to_grade = [
            (i, path)
            for i, path in enumerate(student_files)
            if path.stem not in already_graded
        ]

    # Validate question groups once (not per student)
    groups = get_effective_question_groups(grading_config)
    ungrouped = validate_question_groups(groups, solution_parsed)
    if grade_only:
        print(f"Grade only: {grade_only} — skipping {len(ungrouped)} other questions")
        logger.info(
            "grade_only=%s, skipping %d questions, grading %d students",
            grade_only,
            len(ungrouped),
            len(to_grade),
        )
    else:
        if ungrouped:
            print(f"Warning: Questions not in any group (will score 0): {ungrouped}")
            logger.warning("Questions not in any group (will score 0): %s", ungrouped)
        logger.info("Grading %d students", len(to_grade))

    workers = cfg.workers
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    model = cfg.model or DEFAULT_MODEL

    if workers <= 1 or len(to_grade) <= 1:
        # Sequential grading
        graded_count = 0
        for i, path in to_grade:
            student_name = path.stem
            yield {
                "student": student_name,
                "status": "working",
                "result": None,
                "error": None,
                "index": i + 1,
                "total": len(student_files),
            }
            try:
                student_parsed = json.loads(path.read_text(encoding="utf-8"))
                merge_into = (
                    results[results_by_name[student_name]]
                    if student_name in results_by_name and grade_only_merge
                    else None
                )
                result = grade_student(
                    student_parsed,
                    solution_parsed,
                    cfg,
                    client,
                    ungrouped=ungrouped,
                    merge_into=merge_into,
                )
                u = result.pop("_usage", None)
                if u:
                    usage_total["prompt_tokens"] += u.get("prompt_tokens", 0)
                    usage_total["completion_tokens"] += u.get("completion_tokens", 0)
                if results_lock:
                    with results_lock:
                        _store_result(student_name, result)
                else:
                    _store_result(student_name, result)
                graded_count += 1
                yield {
                    "student": student_name,
                    "status": "done",
                    "result": result,
                    "error": None,
                    "index": i + 1,
                    "total": len(student_files),
                }
            except Exception as e:
                logger.exception("Failed to grade %s", student_name)
                yield {
                    "student": student_name,
                    "status": "error",
                    "result": None,
                    "error": str(e),
                    "index": i + 1,
                    "total": len(student_files),
                }

        # Final usage summary
        if usage_total["prompt_tokens"] or usage_total["completion_tokens"]:
            inp, out = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
            cost = (usage_total["prompt_tokens"] / 1e6 * inp) + (
                usage_total["completion_tokens"] / 1e6 * out
            )
            logger.info(
                "Grading complete: %d students, %d tokens (%.0f in / %.0f out), ~$%.4f",
                graded_count,
                usage_total["prompt_tokens"] + usage_total["completion_tokens"],
                usage_total["prompt_tokens"],
                usage_total["completion_tokens"],
                cost,
            )
            yield {
                "student": "",
                "status": "usage",
                "result": None,
                "error": None,
                "usage": usage_total,
                "cost_usd": round(cost, 4),
                "model": model,
            }
    else:
        # Parallel grading
        def _grade_one(args):
            i, path, merge_into = args
            student_name = path.stem
            try:
                student_parsed = json.loads(path.read_text(encoding="utf-8"))
                result = grade_student(
                    student_parsed,
                    solution_parsed,
                    cfg,
                    client,
                    ungrouped=ungrouped,
                    merge_into=merge_into,
                )
                return (i, student_name, "done", result, None)
            except Exception as e:
                logger.exception("Failed to grade %s", student_name)
                return (i, student_name, "error", None, str(e))

        graded_count = 0
        to_grade_with_merge = [
            (
                i,
                path,
                (
                    results[results_by_name[path.stem]]
                    if path.stem in results_by_name and grade_only_merge
                    else None
                ),
            )
            for i, path in to_grade
        ]
        for i, path in to_grade:
            yield {
                "student": path.stem,
                "status": "working",
                "result": None,
                "error": None,
                "index": i + 1,
                "total": len(student_files),
            }
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_grade_one, item): item for item in to_grade_with_merge
            }
            for future in as_completed(futures):
                i, student_name, status, result, error = future.result()
                if status == "done":
                    u = result.pop("_usage", None)
                    if u:
                        usage_total["prompt_tokens"] += u.get("prompt_tokens", 0)
                        usage_total["completion_tokens"] += u.get(
                            "completion_tokens", 0
                        )
                    assert (
                        result is not None
                    )  # status == "done" always has a dict result
                    if results_lock:
                        with results_lock:
                            _store_result(student_name, result)
                    else:
                        _store_result(student_name, result)
                    graded_count += 1
                yield {
                    "student": student_name,
                    "status": status,
                    "result": result,
                    "error": error,
                    "index": i + 1,
                    "total": len(student_files),
                }

        if usage_total["prompt_tokens"] or usage_total["completion_tokens"]:
            inp, out = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
            cost = (usage_total["prompt_tokens"] / 1e6 * inp) + (
                usage_total["completion_tokens"] / 1e6 * out
            )
            logger.info(
                "Grading complete: %d students, %d tokens (%.0f in / %.0f out), ~$%.4f",
                graded_count,
                usage_total["prompt_tokens"] + usage_total["completion_tokens"],
                usage_total["prompt_tokens"],
                usage_total["completion_tokens"],
                cost,
            )
            yield {
                "student": "",
                "status": "usage",
                "result": None,
                "error": None,
                "usage": usage_total,
                "cost_usd": round(cost, 4),
                "model": model,
            }
