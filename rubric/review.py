"""Parallel/sequential rubric review orchestration."""

import threading

from llm.parallel import iter_unordered_parallel_results
from rubric.jobs import RubricReviewJob
from rubric.prompts import get_rubric_review_prompt
from rubric.review_one import review_one_group
from utils import (
    AppConfig,
    DEFAULT_MODEL,
    ensure_app_config,
    filter_groups_by_grade_only,
    get_job_logger,
    get_openai_client,
)


def review_rubrics(
    rubrics: dict[str, dict],
    config: AppConfig | dict,
    solution_parsed: dict,
    client=None,
    groups_to_review: list[list[str]] | None = None,
) -> dict[str, dict]:
    """Optional second pass: review generated rubrics against question text."""
    logger = get_job_logger(config, __name__)
    cfg = ensure_app_config(config)
    if client is None:
        client = get_openai_client()

    model = cfg.rubric_model or cfg.model or DEFAULT_MODEL
    max_tokens = cfg.max_completion_tokens
    grading_config = cfg.grading
    groups: list[list[str]] = groups_to_review or grading_config.question_groups
    grade_only: list[str] | None = grading_config.grade_only

    if groups_to_review is None and grade_only is not None:
        groups = filter_groups_by_grade_only(groups, grade_only)

    review_prompt = get_rubric_review_prompt(cfg)

    revised = dict(rubrics)
    revised_in_pass = 0
    workers = cfg.workers
    to_process: list[RubricReviewJob] = [
        RubricReviewJob(
            group=group,
            rubrics=rubrics,
            solution_parsed=solution_parsed,
            config=cfg,
            review_prompt=review_prompt,
            model=model,
            max_completion_tokens=max_tokens,
            client=client,
        )
        for group in groups
        if group
    ]
    groups_reviewed = len(to_process)

    if workers <= 1 or len(to_process) <= 1:
        for job in to_process:
            result = review_one_group(job)
            revised_in_pass += len(result)
            revised.update(result)
    else:
        logger.info(
            "Rubric review started in parallel: %d groups, %d workers, model=%s",
            len(to_process),
            workers,
            model,
        )
        review_lock = threading.Lock()
        for result in iter_unordered_parallel_results(
            to_process,
            review_one_group,
            max_workers=workers,
        ):
            with review_lock:
                revised_in_pass += len(result)
                revised.update(result)

    logger.info(
        "Rubric review complete — %d groups reviewed, %d questions revised in pass",
        groups_reviewed,
        revised_in_pass,
    )
    return revised
