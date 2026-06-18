"""Rubric review pass: optional second LLM pass over generated rubrics."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass

from pydantic import BaseModel

from config_models import (
    AppConfig,
    DEFAULT_MODEL,
    RubricEntry,
    sanitize_llm_text,
)
from grading_models import RubricReviewResponse
from grading_helpers import filter_groups_by_grade_only
from llm.json_runner import (
    LlmContext,
    MAX_JSON_LLM_ATTEMPTS,
    execute_llm_task,
    extract_llm_questions,
    load_llm_context,
    run_jobs,
)
from prompt_builder import get_question_data


@dataclass(frozen=True)
class RubricReviewJob:
    group: list[str]
    rubrics: dict[str, RubricEntry]
    solution_parsed: dict
    config: AppConfig
    ctx: LlmContext


def _rubric_as_dict(entry: RubricEntry | dict) -> dict:
    if isinstance(entry, RubricEntry):
        return entry.model_dump()
    return entry


def _postprocess_rubric_review(
    parsed: BaseModel,
    *,
    group_rubrics: dict[str, dict],
) -> dict[str, RubricEntry]:
    assert isinstance(parsed, RubricReviewResponse)
    by_qid = extract_llm_questions(
        parsed.questions,
        expected_qids=list(group_rubrics.keys()),
        get_qid=lambda q: q.question_id,
    )
    revised: dict[str, RubricEntry] = {}
    for qid, q in by_qid.items():
        original = RubricEntry.model_validate(group_rubrics[qid])
        if len(q.items) != len(original.items):
            revised[qid] = original
            continue
        new_items = [
            {
                "description": sanitize_llm_text(ni.description.strip())
                or oi.description,
                "deduction": oi.deduction,
            }
            for ni, oi in zip(q.items, original.items)
        ]
        try:
            revised[qid] = RubricEntry.model_validate(
                {"points": original.points, "items": new_items}
            )
        except Exception:
            revised[qid] = original
    return revised


def review_one_group(job: RubricReviewJob) -> dict[str, RubricEntry]:
    """Review rubrics for one group; return revised qid -> rubric entries."""
    if job.config.agentic.enabled:
        return _review_one_group_agentic(job)
    return _review_one_group_simple(job)


def _review_one_group_agentic(job: RubricReviewJob) -> dict[str, RubricEntry]:
    from agentic.postprocess import merge_reviewed_rubric
    from agentic.rubric_crew import run_rubric_review

    group = job.group
    rubrics = job.rubrics
    solution_parsed = job.solution_parsed
    config = job.config
    ctx = job.ctx

    try:
        raw, _usage, group_rubrics = run_rubric_review(
            group,
            rubrics,
            solution_parsed,
            config,
            crew_type=config.agentic.crew_type,
        )
        if not group_rubrics:
            return {}
        return merge_reviewed_rubric(raw, group_rubrics)
    except Exception as e:
        ctx.logger.warning("Agentic rubric review failed for group %s: %s", group, e)
        return {}


def _review_one_group_simple(job: RubricReviewJob) -> dict[str, RubricEntry]:
    group = job.group
    rubrics = job.rubrics
    solution_parsed = job.solution_parsed
    ctx = job.ctx

    parts: list[str] = []
    group_rubrics: dict[str, dict] = {}
    for qid in group:
        if qid not in rubrics:
            continue
        rd = _rubric_as_dict(rubrics[qid])
        sol_q = get_question_data(solution_parsed, qid)
        q_md = (sol_q or {}).get("question_markdown", f"Question {qid}")
        pts = rd.get("points", 0)
        parts.append(f"--- QUESTION {qid} ({pts} pts) ---")
        parts.append(f"QUESTION TEXT:\n{q_md}\n")
        parts.append(f"CURRENT RUBRIC:\n{json.dumps({qid: rd}, indent=2)}\n")
        group_rubrics[qid] = rd

    if not parts:
        return {}

    messages = [
        {"role": "system", "content": ctx.system_prompt},
        {"role": "user", "content": "\n".join(parts)},
    ]

    def _exhausted(last_err: BaseException | None) -> dict[str, RubricEntry]:
        ctx.logger.warning(
            "Rubric review failed for group %s after %d attempts: %s",
            group,
            MAX_JSON_LLM_ATTEMPTS,
            last_err,
        )
        return {}

    ctx.logger.info("Rubric review - group: %s (model=%s)", group, ctx.model)

    revised, usage = execute_llm_task(
        ctx.client,
        model=ctx.model,
        messages=messages,
        max_completion_tokens=ctx.max_completion_tokens,
        response_model=RubricReviewResponse,
        task_kind="rubric_review",
        logger=ctx.logger,
        postprocess=lambda p: _postprocess_rubric_review(
            p, group_rubrics=group_rubrics
        ),
        fallback_factory=_exhausted,
    )

    ctx.logger.info(
        "Rubric review - group complete: %d/%d questions revised (tokens: %d in / %d out)",
        len(revised),
        len(group_rubrics),
        usage.prompt_tokens,
        usage.completion_tokens,
    )
    return revised


def review_rubrics(
    rubrics: dict[str, RubricEntry],
    config: AppConfig,
    solution_parsed: dict,
    client=None,
    groups_to_review: list[list[str]] | None = None,
) -> dict[str, RubricEntry]:
    """Optional second pass: review generated rubrics against question text."""
    ctx = load_llm_context(
        config,
        "review_system",
        model_selector=lambda c: c.rubric_model or c.model or DEFAULT_MODEL,
        client=client,
    )

    grading_config = config.grading
    groups: list[list[str]] = groups_to_review or grading_config.question_groups
    grade_only: list[str] | None = grading_config.grade_only

    if groups_to_review is None and grade_only is not None:
        groups = filter_groups_by_grade_only(groups, grade_only)

    revised = dict(rubrics)
    revised_in_pass = 0
    to_process: list[RubricReviewJob] = [
        RubricReviewJob(
            group=group,
            rubrics=rubrics,
            solution_parsed=solution_parsed,
            config=config,
            ctx=ctx,
        )
        for group in groups
        if group
    ]
    groups_reviewed = len(to_process)

    if ctx.workers > 1 and len(to_process) > 1:
        ctx.logger.info(
            "Rubric review started in parallel: %d groups, %d workers, model=%s",
            len(to_process),
            ctx.workers,
            ctx.model,
        )
    review_lock = threading.Lock()
    for result in run_jobs(to_process, review_one_group, max_workers=ctx.workers):
        with review_lock:
            revised_in_pass += len(result)
            revised.update(result)

    ctx.logger.info(
        "Rubric review complete — %d groups reviewed, %d questions revised in pass",
        groups_reviewed,
        revised_in_pass,
    )
    return revised
