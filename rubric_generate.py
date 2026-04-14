"""Rubric generation: prompts, LLM worker, and orchestration (optional review in rubric_review)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from openai import OpenAI
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
from llm.json_runner import MAX_JSON_LLM_ATTEMPTS, execute_llm_task, extract_llm_questions, run_jobs
from token_usage import TokenUsage, usage_cost_usd
from prompt_builder import get_question_data, load_prompt, truncate_output
from utils import (
    get_job_logger,
    get_openai_client,
)

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


def build_rubric_group_prompt(group: list[str], solution_parsed: dict) -> str:
    """Build the user prompt for one question group."""
    parts: list[str] = []
    max_output_chars = 2000

    for qid in group:
        sol_q = get_question_data(solution_parsed, qid)
        pts = (sol_q or {}).get("points", 0)
        q_md = (sol_q or {}).get("question_markdown", f"Question {qid}")

        block = f"--- QUESTION {qid} ({pts} pts) ---\n{q_md}\n\n"
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
    rubric_prompt: str
    model: str
    max_completion_tokens: int
    client: OpenAI | None


def generate_one_group(
    job: RubricGenJob,
) -> tuple[int, list[str], dict[str, RubricEntry], TokenUsage, bool]:
    """Generate rubric for one group. Returns (idx, group, rubrics_for_group, usage, had_error)."""
    idx = job.idx
    group = job.group
    solution_parsed = job.solution_parsed
    config = job.config
    rubric_prompt = job.rubric_prompt
    model = job.model
    max_completion_tokens = job.max_completion_tokens
    client = job.client
    log = get_job_logger(config, __name__)
    if client is None:
        client = get_openai_client()
    rubrics_for_group: dict[str, RubricEntry] = {}
    usage = TokenUsage()
    had_error = False

    if not group:
        return (idx, group, rubrics_for_group, usage, had_error)

    user_content = build_rubric_group_prompt(group, solution_parsed)
    messages = [
        {"role": "system", "content": rubric_prompt},
        {"role": "user", "content": user_content},
    ]

    g_tuple = tuple(group)

    def _exhausted(last_err: BaseException | None) -> dict[str, RubricEntry]:
        nonlocal had_error
        had_error = True
        log.error(
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

    log.info(
        "Rubric generation - group %d: %s (model=%s)",
        idx + 1,
        group,
        model,
    )

    rubrics_for_group, usage = execute_llm_task(
        client,
        model=model,
        messages=messages,
        max_completion_tokens=max_completion_tokens,
        response_model=RubricGroupLlmResponse,
        task_kind="rubric_generate",
        logger=log,
        postprocess=lambda p: _postprocess_rubric_generate(p, group=g_tuple),
        fallback_factory=_exhausted,
    )

    log.info(
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

    logger = get_job_logger(config, __name__)
    solution_parsed = load_solution_parsed(config)

    if client is None:
        client = get_openai_client()

    grading_config = config.grading
    all_groups: list[list[str]] = grading_config.question_groups
    grade_only: list[str] | None = grading_config.grade_only
    model = config.rubric_model or config.model or DEFAULT_MODEL
    workers = config.workers
    max_completion_tokens = config.max_completion_tokens
    rubric_prompt = load_prompt("rubric_system", assignment_name=config.assignment_name)

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
            rubric_prompt=rubric_prompt,
            model=model,
            max_completion_tokens=max_completion_tokens,
            client=client,
        )
        for idx, group in enumerate(groups)
        if group
    ]

    if workers > 1 and len(to_process) > 1:
        logger.info(
            "Rubric generation started in parallel: %d groups, %d workers, model=%s",
            len(to_process),
            workers,
            model,
        )
    for (
        idx,
        group,
        rubrics_for_group,
        usage,
        had_error,
    ) in run_jobs(to_process, generate_one_group, max_workers=workers):
        with rubrics_lock:
            rubrics.update(rubrics_for_group)
            rubrics_snapshot = {q: e.model_dump() for q, e in rubrics.items()}
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

    if config.rubric_review:
        logger.info("Running rubric review pass...")
        rubrics = review_rubrics(
            rubrics,
            config,
            solution_parsed,
            client,
            groups_to_review=groups if partial else None,
        )

    return rubrics
