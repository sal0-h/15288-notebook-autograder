"""Rubric generation orchestration (LLM + optional review)."""

import json
import threading
from collections.abc import Callable

from llm.cost import usage_cost_usd
from llm.parallel import iter_unordered_parallel_results
from llm.types import TokenUsage
from rubric.generate_one import generate_one_group
from rubric.jobs import RubricGenJob
from rubric.prompts import get_rubric_generation_prompt
from rubric.review import review_rubrics
from utils import (
    AppConfig,
    DEFAULT_MODEL,
    ensure_app_config,
    filter_groups_by_grade_only,
    get_assignment_output_paths,
    get_job_logger,
    get_openai_client,
)


def generate_rubrics(
    config: AppConfig | dict,
    client=None,
    progress_callback: Callable[[int, int, list, dict], None] | None = None,
    group_indices: list[int] | None = None,
) -> dict[str, dict[str, int | str]]:
    """
    Generate grading rubrics from solution_parsed.json using the LLM.

    Uses question_groups from config. One LLM call per group.
    When grade_only is set, only generates for those questions.
    When group_indices is set, only generates for those groups (0-based) and merges
    into existing rubrics (does not overwrite others).
    Uses config.workers (default 1) for parallel generation.
    progress_callback(group_index, total_groups, group, rubrics_so_far) is called after each group.
    Returns a dict: { "qid": {"points": N, "items": [{"description": "...", "deduction": ...}], ...} }
    """
    logger = get_job_logger(config, __name__)
    cfg = ensure_app_config(config)
    paths = get_assignment_output_paths(cfg)
    solution_path = paths.solution_parsed

    if not solution_path.exists():
        raise FileNotFoundError(
            f"Solution parsed not found: {solution_path}. Run the parse step first."
        )

    if client is None:
        client = get_openai_client()

    solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
    grading_config = cfg.grading
    all_groups: list[list[str]] = grading_config.question_groups
    grade_only: list[str] | None = grading_config.grade_only
    model = cfg.rubric_model or cfg.model or DEFAULT_MODEL
    workers = cfg.workers
    max_completion_tokens = cfg.max_completion_tokens
    rubric_prompt = get_rubric_generation_prompt(cfg)

    partial = group_indices is not None
    if group_indices is not None:
        valid = [i for i in group_indices if 0 <= i < len(all_groups)]
        if not valid:
            raise ValueError(
                f"Invalid group_indices {group_indices}. Valid range: 0–{len(all_groups) - 1}."
            )
        groups = [all_groups[i] for i in sorted(set(valid))]
    else:
        groups = list(all_groups)

    if grade_only is not None:
        groups = filter_groups_by_grade_only(groups, grade_only)

    rubrics: dict[str, dict] = (
        {qid: entry.model_dump() for qid, entry in cfg.rubrics.items()}
        if partial
        else {}
    )
    usage_total = TokenUsage()
    groups_failed = 0
    rubrics_lock = threading.Lock()
    total = len(groups)
    to_process = [
        RubricGenJob(
            idx=idx,
            group=group,
            solution_parsed=solution_parsed,
            config=cfg,
            rubric_prompt=rubric_prompt,
            model=model,
            max_completion_tokens=max_completion_tokens,
            client=client,
        )
        for idx, group in enumerate(groups)
        if group
    ]

    if workers <= 1 or len(to_process) <= 1:
        for job in to_process:
            idx, group = job.idx, job.group
            logger.info(
                "Rubric generation progress: group %d/%d (%s)",
                idx + 1,
                total,
                group,
            )
            if progress_callback:
                progress_callback(idx + 1, total, group, dict(rubrics))
            _, _, rubrics_for_group, usage, had_error = generate_one_group(job)
            rubrics.update(rubrics_for_group)
            usage_total = usage_total.merged(usage)
            if had_error:
                groups_failed += 1
            if progress_callback:
                progress_callback(idx + 1, total, group, dict(rubrics))
    else:
        logger.info(
            "Rubric generation started in parallel: %d groups, %d workers, model=%s",
            len(to_process),
            workers,
            model,
        )
        if progress_callback:
            for idx, group in enumerate(groups):
                if group:
                    progress_callback(idx + 1, total, group, {})
        for idx, group, rubrics_for_group, usage, had_error in iter_unordered_parallel_results(
            to_process,
            generate_one_group,
            max_workers=workers,
        ):
            with rubrics_lock:
                rubrics.update(rubrics_for_group)
                rubrics_snapshot = dict(rubrics)
            usage_total = usage_total.merged(usage)
            if had_error:
                groups_failed += 1
            logger.info(
                "Rubric generation progress: group %d/%d complete (%s)",
                idx + 1,
                total,
                group,
            )
            if progress_callback:
                progress_callback(idx + 1, total, group, rubrics_snapshot)

    if usage_total.has_tokens():
        cost = usage_cost_usd(usage_total, model)
        logger.info(
            "Rubric generation complete: %d groups, %d failed, %d questions, %d tokens (%.0f in / %.0f out), ~$%.4f",
            len(to_process),
            groups_failed,
            len(rubrics),
            usage_total.total_tokens,
            usage_total.prompt_tokens,
            usage_total.completion_tokens,
            cost,
        )
    else:
        logger.info(
            "Rubric generation complete: %d groups, %d failed, %d questions",
            len(to_process),
            groups_failed,
            len(rubrics),
        )

    if cfg.rubric_review:
        logger.info("Running rubric review pass...")
        rubrics = review_rubrics(
            rubrics,
            cfg,
            solution_parsed,
            client,
            groups_to_review=groups if partial else None,
        )

    return rubrics
