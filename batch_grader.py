"""Batch grading orchestration — sequential and parallel, with resume support."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

from openai import OpenAI

from llm.types import TokenUsage
from llm.usage_helpers import (
    detach_usage_from_graded_result,
    graded_usage_summary_event,
    merge_graded_usage,
)
from llm.parallel import iter_unordered_parallel_results
from prompt_builder import validate_question_groups
from results_store import load_results_with_backup, save_results, update_student
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
    results: list[dict]
    results_by_name: dict[str, int]
    out_path: Path
    ungrouped: list[str]
    grade_only_merge: bool


def load_grade_queue(
    config: AppConfig | dict, logger_obj: logging.Logger | None = None
) -> GradeQueue:
    """
    Load solution + parsed students + existing results and compute the grade queue.
    Raises FileNotFoundError if solution_parsed.json is missing.
    """
    cfg = ensure_app_config(config)
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
    raw = load_results_with_backup(out_path, logger_obj=logger_obj)
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
    else:
        to_grade = [
            (i, path)
            for i, path in enumerate(student_files)
            if path.stem not in already_graded
        ]

    groups = get_effective_question_groups(grading_config)
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
    grade_only = get_active_grade_only(grading_config)

    def _store_result(student_name: str, result: dict) -> None:
        """Update results list/index and persist — call with lock held if parallel."""
        update_student(results, student_name, result, logger_obj=logger)
        if student_name not in results_by_name:
            results_by_name[student_name] = len(results) - 1
        save_results(out_path, results, logger_obj=logger)

    if grade_only_merge:
        skipped = len(student_files) - len(to_grade)
        print(
            f"grade_only_merge: grading {len(to_grade)} students (skipping {skipped} already graded for grade_only)"
        )

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
                usage_total = merge_graded_usage(usage_total, result)
                result, _ = detach_usage_from_graded_result(result)
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
        for i, student_name, status, result, error in iter_unordered_parallel_results(
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
                assert result is not None  # status == "done" always has a dict result
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
