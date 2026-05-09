"""Rubric generation: prompts, LLM worker, and orchestration (optional review in rubric_review)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel

from config_models import (
    AppConfig,
    DEFAULT_MODEL,
    RubricEntry,
    RubricItem,
    load_solution_parsed,
)
from grading_models import RubricGroupLlmResponse
from grading_helpers import filter_groups_by_grade_only
from llm.json_runner import (
    LlmContext,
    MAX_JSON_LLM_ATTEMPTS,
    execute_llm_task,
    extract_llm_questions,
    load_llm_context,
    run_jobs,
)
from token_usage import TokenUsage, usage_cost_usd
from prompt_builder import get_question_data, truncate_output

GENERATION_FAILED = "[generation failed]"


def _postprocess_rubric_generate(
    parsed: BaseModel,
    *,
    group: tuple[str, ...],
) -> dict[str, RubricEntry]:
    assert isinstance(parsed, RubricGroupLlmResponse)
    by_qid = extract_llm_questions(
        parsed.questions,
        expected_qids=group,
        get_qid=lambda q: q.question_id,
    )
    built: dict[str, RubricEntry] = {}
    for qid, q in by_qid.items():
        raw = {
            "points": q.points,
            "items": [
                {"description": item.description, "deduction": item.deduction}
                for item in q.items
            ],
        }
        built[qid] = RubricEntry.from_llm_output(raw)
    return built


def build_rubric_group_prompt(
    group: list[str],
    solution_parsed: dict,
    assignment_name: str | None = None,
) -> str:
    """Build the user prompt for one question group."""
    from prompt_builder import _load_question_type_instructions

    parts: list[str] = []
    max_output_chars = 2000
    type_instructions = _load_question_type_instructions(assignment_name)

    for qid in group:
        sol_q = get_question_data(solution_parsed, qid)
        pts = (sol_q or {}).get("points", 0)
        q_md = (sol_q or {}).get("question_markdown", f"Question {qid}")

        block = f"--- QUESTION {qid} ({pts} pts) ---\n{q_md}\n\n"

        # Inject question-type instruction so rubric criteria match grading behavior
        q_type = (sol_q or {}).get("question_type", "mixed")
        type_hint = type_instructions.get(q_type, "").strip()
        if type_hint:
            block += f"{type_hint}\n\n"

        if sol_q:
            if sol_q.get("answer_code_concat"):
                block += f"REFERENCE CODE:\n{truncate_output(sol_q['answer_code_concat'], max_output_chars)}\n\n"
            if sol_q.get("answer_text_concat"):
                block += f"REFERENCE OUTPUT:\n{truncate_output(sol_q['answer_text_concat'], max_output_chars)}\n\n"
            if sol_q.get("answer_markdown_concat"):
                block += f"REFERENCE ANSWER:\n{truncate_output(sol_q['answer_markdown_concat'], max_output_chars)}\n\n"
        parts.append(block)

    return "\n".join(parts)


@dataclass(frozen=True)
class RubricGenJob:
    idx: int
    group: list[str]
    solution_parsed: dict
    config: AppConfig
    ctx: LlmContext


def generate_one_group(
    job: RubricGenJob,
) -> tuple[int, list[str], dict[str, RubricEntry], TokenUsage, bool]:
    """Generate rubric for one group. Returns (idx, group, rubrics_for_group, usage, had_error)."""
    idx = job.idx
    group = job.group
    solution_parsed = job.solution_parsed
    config = job.config
    ctx = job.ctx
    rubrics_for_group: dict[str, RubricEntry] = {}
    usage = TokenUsage()
    had_error = False

    if not group:
        return (idx, group, rubrics_for_group, usage, had_error)

    user_content = build_rubric_group_prompt(
        group, solution_parsed, assignment_name=config.assignment_name
    )
    messages = [
        {"role": "system", "content": ctx.system_prompt},
        {"role": "user", "content": user_content},
    ]

    g_tuple = tuple(group)

    def _exhausted(last_err: BaseException | None) -> dict[str, RubricEntry]:
        nonlocal had_error
        had_error = True
        ctx.logger.error(
            "Rubric generation failed for group %s after %d attempts: %s",
            group,
            MAX_JSON_LLM_ATTEMPTS,
            last_err,
        )
        out: dict[str, RubricEntry] = {}
        for qid in group:
            sol_q = get_question_data(solution_parsed, qid)
            pts = int((sol_q or {}).get("points", 0))
            out[qid] = RubricEntry(
                points=pts,
                items=[
                    RubricItem(description=GENERATION_FAILED, deduction=float(pts)),
                ],
            )
        return out

    ctx.logger.info(
        "Rubric generation - group %d: %s (model=%s)",
        idx + 1,
        group,
        ctx.model,
    )

    rubrics_for_group, usage = execute_llm_task(
        ctx.client,
        model=ctx.model,
        messages=messages,
        max_completion_tokens=ctx.max_completion_tokens,
        response_model=RubricGroupLlmResponse,
        task_kind="rubric_generate",
        logger=ctx.logger,
        postprocess=lambda p: _postprocess_rubric_generate(p, group=g_tuple),
        fallback_factory=_exhausted,
    )

    ctx.logger.info(
        "Rubric generation - group %d complete: %d/%d questions parsed (tokens: %d in / %d out)",
        idx + 1,
        len(rubrics_for_group),
        len(group),
        usage.prompt_tokens,
        usage.completion_tokens,
    )

    return (idx, group, rubrics_for_group, usage, had_error)


def generate_rubrics(
    config: AppConfig,
    client=None,
    progress_callback: Callable[[int, int, list, dict], None] | None = None,
    group_indices: list[int] | None = None,
) -> dict[str, RubricEntry]:
    """
    Generate grading rubrics from solution_parsed.json using the LLM.

    Uses question_groups from config. One LLM call per group.
    When grade_only is set, only generates for those questions.
    When group_indices is set, only generates for those groups (0-based) and merges
    into existing rubrics (does not overwrite others).
    Uses config.workers (default 1) for parallel generation.
    progress_callback(group_index, total_groups, group, rubrics_so_far) is called after each group.
    Returns ``qid -> RubricEntry`` (use ``.model_dump()`` when serializing to YAML/JSON).
    """
    from rubric_review import review_rubrics

    ctx = load_llm_context(
        config,
        "rubric_system",
        model_selector=lambda c: c.rubric_model or c.model or DEFAULT_MODEL,
        client=client,
    )
    solution_parsed = load_solution_parsed(config)

    grading_config = config.grading
    all_groups: list[list[str]] = grading_config.question_groups
    grade_only: list[str] | None = grading_config.grade_only

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

    rubrics: dict[str, RubricEntry] = (
        {qid: entry for qid, entry in config.rubrics.items()} if partial else {}
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
            config=config,
            ctx=ctx,
        )
        for idx, group in enumerate(groups)
        if group
    ]

    if ctx.workers > 1 and len(to_process) > 1:
        ctx.logger.info(
            "Rubric generation started in parallel: %d groups, %d workers, model=%s",
            len(to_process),
            ctx.workers,
            ctx.model,
        )
    for (
        idx,
        group,
        rubrics_for_group,
        usage,
        had_error,
    ) in run_jobs(to_process, generate_one_group, max_workers=ctx.workers):
        with rubrics_lock:
            rubrics.update(rubrics_for_group)
            rubrics_snapshot = {q: e.model_dump() for q, e in rubrics.items()}
        usage_total = usage_total.merged(usage)
        if had_error:
            groups_failed += 1
        ctx.logger.info(
            "Rubric generation progress: group %d/%d complete (%s)",
            idx + 1,
            total,
            group,
        )
        if progress_callback:
            progress_callback(idx + 1, total, group, rubrics_snapshot)

    if usage_total.has_tokens():
        cost = usage_cost_usd(usage_total, ctx.model)
        ctx.logger.info(
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
        ctx.logger.info(
            "Rubric generation complete: %d groups, %d failed, %d questions",
            len(to_process),
            groups_failed,
            len(rubrics),
        )

    if config.rubric_review:
        ctx.logger.info("Running rubric review pass...")
        rubrics = review_rubrics(
            rubrics,
            config,
            solution_parsed,
            ctx.client,
            groups_to_review=groups if partial else None,
        )

    return rubrics
