"""Batch grading orchestration — sequential and parallel, with resume support."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

from openai import OpenAI

from config_models import AppConfig, DEFAULT_MODEL
from grading_helpers import (
    grade_only_list,
    effective_groups,
    needs_merge,
)
from results_models import (
    GradedResult,
    TokenUsage,
    detach_usage_from_graded_result,
    graded_usage_summary_event,
    merge_graded_usage,
)
from llm.json_runner import run_jobs
from prompt_builder import validate_question_groups
from results_store import load_results, save_results, update_student
from utils import (
    get_openai_client,
    get_job_logger,
    get_assignment_output_paths,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GradeQueue:
    """Parsed submissions + who still needs grading (same rules as grade_all_students)."""

    cfg: AppConfig
    solution_parsed: dict
    student_files: list[Path]
    to_grade: list[tuple[int, Path]]
    results: list[GradedResult]
    results_by_name: dict[str, int]
    out_path: Path
    ungrouped: list[str]
    grade_only_merge: bool


def load_grade_queue(
    cfg: AppConfig, logger_obj: logging.Logger | None = None
) -> GradeQueue:
    """
    Load solution + parsed students + existing results and compute the grade queue.
    Raises FileNotFoundError if solution_parsed.json is missing.
    """
    paths = get_assignment_output_paths(cfg)
    solution_path = paths.solution_parsed
    if not solution_path.exists():
        raise FileNotFoundError(
            f"Solution parsed not found: {solution_path}. Run the parse step first."
        )
    solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
    parsed_dir = paths.parsed_dir
    student_files = sorted(parsed_dir.glob("*.json")) if parsed_dir.exists() else []
    out_path = paths.graded_results
    results: list[GradedResult] = load_results(out_path, logger_obj=logger_obj)
    grading_config = cfg.grading

    already_graded = {r.student_name for r in results if r.student_name}
    results_by_name = {
        r.student_name: idx for idx, r in enumerate(results) if r.student_name
    }

    grade_only_merge = grading_config.grade_only_merge
    grade_only = grade_only_list(grading_config)

    if grade_only_merge:
        to_grade = [
            (i, path)
            for i, path in enumerate(student_files)
            if needs_merge(
                (
                    results[results_by_name[path.stem]]
                    if path.stem in results_by_name
                    else None
                ),
                grade_only,
            )
        ]
    else:
        to_grade = [
            (i, path)
            for i, path in enumerate(student_files)
            if path.stem not in already_graded
        ]

    groups = effective_groups(grading_config)
    ungrouped = validate_question_groups(groups, solution_parsed)

    return GradeQueue(
        cfg=cfg,
        solution_parsed=solution_parsed,
        student_files=student_files,
        to_grade=to_grade,
        results=results,
        results_by_name=results_by_name,
        out_path=out_path,
        ungrouped=ungrouped,
        grade_only_merge=grade_only_merge,
    )


def grade_all_students(
    cfg: AppConfig,
    client: OpenAI | None = None,
    results_lock=None,
) -> Generator[dict, None, None]:
    """
    Grade all students sequentially or in parallel.
    Yields progress events; saves graded_results.json after each student.
    When results_lock is provided (e.g. from app), uses it for thread-safe writes.
    """
    logger = get_job_logger(cfg, __name__)

    # Import here to avoid circular dependency (grade imports batch_grader for main())
    from grade import grade_student  # noqa: PLC0415

    if client is None:
        pool_size = max(1, int(cfg.workers))
        client = get_openai_client(
            max_connections=pool_size,
            max_keepalive_connections=pool_size,
            read_timeout_s=120.0,
        )

    paths = get_assignment_output_paths(cfg)
    output_dir = paths.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    if results_lock:
        with results_lock:
            gq = load_grade_queue(cfg, logger_obj=logger)
    else:
        gq = load_grade_queue(cfg, logger_obj=logger)

    solution_parsed = gq.solution_parsed
    student_files = gq.student_files
    to_grade = gq.to_grade
    results = gq.results
    results_by_name = dict(gq.results_by_name)
    out_path = gq.out_path
    ungrouped = gq.ungrouped
    grade_only_merge = gq.grade_only_merge
    grading_config = cfg.grading
    grade_only = grade_only_list(grading_config)

    def _store_result(student_name: str, result: GradedResult) -> None:
        """Update results list/index and persist — call with lock held if parallel."""
        update_student(results, student_name, result, logger_obj=logger)
        if student_name not in results_by_name:
            results_by_name[student_name] = len(results) - 1
        save_results(out_path, results, logger_obj=logger)

    if grade_only_merge:
        skipped = len(student_files) - len(to_grade)
        logger.info(
            "grade_only_merge: grading %d students (skipping %d already graded for grade_only)",
            len(to_grade),
            skipped,
        )

    if grade_only:
        logger.info(
            "Grade only: %s — skipping %d other questions; grading %d students",
            grade_only,
            len(ungrouped),
            len(to_grade),
        )
    else:
        if ungrouped:
            logger.warning("Questions not in any group (will score 0): %s", ungrouped)
        logger.info("Grading %d students", len(to_grade))

    pending = len(to_grade)
    total_parsed = len(student_files)
    skipped = total_parsed - pending
    next_stem = to_grade[0][1].stem if to_grade else None
    if total_parsed == 0:
        qmsg = "No parsed students. Run Parse first."
    elif pending == 0:
        qmsg = (
            "Everyone already graded — nothing to do. "
            "Remove graded_results.json to re-grade all, or use grade-only merge for partial regrades."
        )
    else:
        qmsg = None
    yield {
        "status": "queue_info",
        "pending": pending,
        "total_parsed": total_parsed,
        "skipped": skipped,
        "next_student": next_stem,
        "message": qmsg,
    }

    workers = cfg.workers
    usage_total = TokenUsage()
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
                existing = (
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
                    merge_into=existing.model_dump(mode="python") if existing else None,
                )
                usage_total = merge_graded_usage(usage_total, result)
                result, _ = detach_usage_from_graded_result(result)
                stored = GradedResult.model_validate(result)
                if results_lock:
                    with results_lock:
                        _store_result(student_name, stored)
                else:
                    _store_result(student_name, stored)
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
        if usage_total.has_tokens():
            cost_evt = graded_usage_summary_event(usage_total, model)
            logger.info(
                "Grading complete: %d students, %d tokens (%.0f in / %.0f out), ~$%.4f",
                graded_count,
                usage_total.total_tokens,
                usage_total.prompt_tokens,
                usage_total.completion_tokens,
                cost_evt["cost_usd"],
            )
            yield cost_evt
    else:
        # Parallel grading — shared client is thread-safe (httpx.Client); connection pooling reduces latency
        def _grade_one(args):
            i, path, existing = args
            student_name = path.stem
            try:
                student_parsed = json.loads(path.read_text(encoding="utf-8"))
                result = grade_student(
                    student_parsed,
                    solution_parsed,
                    cfg,
                    client,
                    ungrouped=ungrouped,
                    merge_into=existing.model_dump(mode="python") if existing else None,
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
        for i, student_name, status, result, error in run_jobs(
            to_grade_with_merge,
            _grade_one,
            max_workers=workers,
        ):
            if status == "done":
                if result is None:
                    logger.error(
                        "Worker returned done without result for %s", student_name
                    )
                    continue
                usage_total = merge_graded_usage(usage_total, result)
                result, _ = detach_usage_from_graded_result(result)
                stored = GradedResult.model_validate(result)
                if results_lock:
                    with results_lock:
                        _store_result(student_name, stored)
                else:
                    _store_result(student_name, stored)
                graded_count += 1
            yield {
                "student": student_name,
                "status": status,
                "result": result,
                "error": error,
                "index": i + 1,
                "total": len(student_files),
            }

        if usage_total.has_tokens():
            cost_evt = graded_usage_summary_event(usage_total, model)
            logger.info(
                "Grading complete: %d students, %d tokens (%.0f in / %.0f out), ~$%.4f",
                graded_count,
                usage_total.total_tokens,
                usage_total.prompt_tokens,
                usage_total.completion_tokens,
                cost_evt["cost_usd"],
            )
            yield cost_evt
