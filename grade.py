"""LLM-based per-group and per-student grading logic."""

import hashlib
import json as _json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from config_models import AppConfig, DEFAULT_MODEL
from grading_helpers import (
    skipped_feedback,
)
from grading_models import (
    GRADING_FAILED,
    GradingLlmResponse,
    NO_SUBMISSION,
    QuestionGrade,
    SKIP_FEEDBACKS,
)
from llm.json_runner import (
    LlmContext,
    MAX_JSON_LLM_ATTEMPTS,
    execute_llm_task,
    extract_llm_questions,
    load_llm_context,
)
from token_usage import TokenUsage
from prompt_builder import (
    build_group_prompt,
    get_question_data,
    validate_question_groups,
)
from results_models import GradedResult
from config_models import load_app_config

_NO_SUBMISSION_PATTERNS = (
    "[no submission]",
    "no submission",
    "not found in the student submission",
    "question was not found",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rubric_hash(rubric_entry) -> str:
    """Short hash of rubric content for provenance tracking."""
    if rubric_entry is None:
        return ""
    data = rubric_entry
    if hasattr(rubric_entry, "model_dump"):
        data = rubric_entry.model_dump(mode="python")
    return hashlib.sha256(
        _json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()[:8]


def _normalize_no_submission_feedback(feedback: str) -> str:
    """If feedback indicates no submission, normalise to the canonical '[no submission]'."""
    if not feedback or not feedback.strip():
        return feedback
    s = feedback.strip().lower()
    if any(pattern in s for pattern in _NO_SUBMISSION_PATTERNS):
        return NO_SUBMISSION
    return feedback


def compute_totals_from_questions(
    questions: dict[str, dict],
) -> tuple[float, float, list[str]]:
    """Compute total score/max over graded questions only and return summary feedback parts."""
    graded_items = [
        (qid, q)
        for qid, q in questions.items()
        if (q.get("feedback") or "").strip() not in SKIP_FEEDBACKS
    ]
    total_score = sum(float(q.get("score", 0)) for _, q in graded_items)
    total_max = sum(float(q.get("max", 0)) for _, q in graded_items)
    feedback_parts = [
        f"Q{qid}: {q.get('feedback', '')}"
        for qid, q in sorted(graded_items)
        if q.get("feedback") and float(q.get("score", 0)) < float(q.get("max", 0))
    ]
    return total_score, total_max, feedback_parts


def _postprocess_grade_group(
    parsed: BaseModel,
    *,
    group: tuple[str, ...],
) -> list[QuestionGrade]:
    assert isinstance(parsed, GradingLlmResponse)
    by_qid = extract_llm_questions(
        parsed.grades,
        expected_qids=group,
        get_qid=lambda g: g.question_id,
    )
    return [
        item.model_copy(update={"question_id": qid}) for qid, item in by_qid.items()
    ]


# ---------------------------------------------------------------------------
# Core grading
# ---------------------------------------------------------------------------


def grade_group(
    group: list[str],
    solution_parsed: dict,
    student_parsed: dict,
    config: AppConfig,
    ctx: LlmContext,
    student_name: str | None = None,
) -> tuple[list[QuestionGrade], dict[str, int], TokenUsage]:
    """
    Grade one question group. Retries up to MAX_VALIDATION_RETRIES times
    if the LLM response fails Pydantic validation.
    """
    messages, qid_to_max = build_group_prompt(
        group,
        solution_parsed,
        student_parsed,
        ctx.system_prompt,
        max_prompt_tokens=config.max_prompt_tokens,
        rubrics=config.rubrics,
        include_reference=config.include_reference_in_grading,
        assignment_name=config.assignment_name,
    )

    name_ctx = f" [{student_name}]" if student_name else ""
    effective_max = min(ctx.max_completion_tokens, max(2048, len(group) * 1024))
    g_tuple = tuple(group)

    def _exhausted(last_err: BaseException | None) -> list[QuestionGrade]:
        ctx.logger.error(
            "Giving up on group %s%s after %d attempts: %s",
            group,
            name_ctx,
            MAX_JSON_LLM_ATTEMPTS,
            last_err,
            extra={"task": "grade_group", "model": ctx.model},
        )
        return [
            QuestionGrade(
                question_id=qid,
                score=0.0,
                feedback=GRADING_FAILED,
                confidence="low",
                requires_review=True,
            )
            for qid in group
        ]

    grade_items, usage = execute_llm_task(
        ctx.client,
        model=ctx.model,
        messages=messages,
        max_completion_tokens=effective_max,
        response_model=GradingLlmResponse,
        task_kind="grade_group",
        logger=ctx.logger,
        postprocess=lambda p: _postprocess_grade_group(p, group=g_tuple),
        fallback_factory=_exhausted,
    )
    return grade_items, qid_to_max, usage


def _store_group_grades(
    questions: dict[str, dict],
    feedback_parts: list[str],
    group: list[str],
    grade_items: list[QuestionGrade],
    qid_to_max: dict[str, int],
    model: str = "",
    rubrics: dict | None = None,
) -> tuple[float, float]:
    """Process one group's grading response into questions dict. Returns (score, max) for this group."""
    grade_map = {g.question_id: g for g in grade_items}
    total_score = 0.0
    total_max = 0.0
    now = datetime.now(timezone.utc).isoformat()
    for qid in group:
        max_pts = qid_to_max.get(qid, 0)
        total_max += max_pts
        q_grade = grade_map.get(
            qid,
            QuestionGrade(
                question_id=qid, score=0.0, feedback="[missing]", confidence="low"
            ),
        )
        score = max(0.0, min(float(max_pts), q_grade.score))
        feedback = _normalize_no_submission_feedback(q_grade.feedback.strip())
        questions[qid] = {
            "score": score,
            "max": max_pts,
            "feedback": feedback,
            "confidence": q_grade.confidence,
            "requires_review": q_grade.requires_review,
            "_provenance": {
                "model": model,
                "rubric_hash": _rubric_hash(rubrics.get(qid) if rubrics else None),
                "graded_at": now,
            },
        }
        total_score += score
        if feedback and score < max_pts:
            feedback_parts.append(f"Q{qid}: {feedback}")
    return total_score, total_max


def _zero_ungrouped_questions(
    questions: dict[str, dict],
    feedback_parts: list[str],
    ungrouped: list[str],
    solution_parsed: dict,
    skip_msg: str,
) -> None:
    """Add zero-score entries for ungrouped/skipped questions."""
    for qid in ungrouped:
        sol_q = get_question_data(solution_parsed, qid)
        max_pts = (sol_q or {}).get("points", 0)
        questions[qid] = {
            "score": 0.0,
            "max": max_pts,
            "feedback": skip_msg,
            "confidence": "low",
            "requires_review": False,
        }
        feedback_parts.append(f"Q{qid}: {skip_msg}")


def _build_graded_result(
    student_name: str,
    questions: dict[str, dict],
    total_score: float,
    total_max: float,
    feedback_parts: list[str],
    usage_total: TokenUsage,
) -> GradedResult:
    """Build the final GradedResult for one student."""
    return GradedResult(
        student_name=student_name,
        questions=questions,
        total_score=round(total_score, 2),
        total_max=round(total_max, 2),
        summary_feedback=(
            ". ".join(feedback_parts) if feedback_parts else "Full marks."
        ),
        usage=usage_total.to_json_dict() if usage_total.has_tokens() else None,
    )


def grade_student(
    student_parsed: dict,
    solution_parsed: dict,
    cfg: AppConfig,
    client=None,
    ungrouped: list[str] | None = None,
    merge_into: GradedResult | dict | None = None,
) -> GradedResult:
    """Grade one student. Returns GradedResult.

    When merge_into is provided with grade_only, only grades grade_only questions
    and merges new grades into existing result (keeps other questions unchanged).
    """
    ctx = load_llm_context(cfg, "grade_system", client=client)

    grading_config = cfg.grading
    groups = grading_config.get_effective_groups()
    grade_only = grading_config.get_grade_only()
    student_name = student_parsed.get("student_name", "Unknown")
    is_merge = merge_into is not None and grade_only is not None

    if ungrouped is None:
        ungrouped = validate_question_groups(groups, solution_parsed)

    questions: dict[str, dict] = {}
    if is_merge and merge_into is not None:
        if isinstance(merge_into, GradedResult):
            questions = {
                qid: q.model_dump(mode="python")
                for qid, q in merge_into.questions.items()
            }
        else:
            # Accept raw dict for backward compatibility (tests, API callers)
            raw_qs = (
                merge_into.get("questions", {}) if isinstance(merge_into, dict) else {}
            )
            questions = {qid: dict(q) for qid, q in raw_qs.items()}
    total_score = 0.0
    total_max = 0.0
    feedback_parts: list[str] = []
    usage_total = TokenUsage()

    ctx.logger.info("Grading %s (%d groups)", student_name, len(groups))
    for group_idx, group in enumerate(groups):
        if not group:
            continue
        ctx.logger.info(
            "Grading %s — group %d/%d: %s",
            student_name,
            group_idx + 1,
            len(groups),
            group,
        )
        all_missing = all(
            get_question_data(student_parsed, qid) is None for qid in group
        )
        if all_missing:
            ctx.logger.info(
                "Skipping group %s for %s (all questions missing)",
                group,
                student_name,
            )
            qid_to_max = {}
            for qid in group:
                sol_q = get_question_data(solution_parsed, qid)
                qid_to_max[qid] = (sol_q or {}).get("points", 0)
            grade_items = [
                QuestionGrade(question_id=qid, score=0.0, feedback=NO_SUBMISSION)
                for qid in group
            ]
            usage = TokenUsage()
        else:
            grade_items, qid_to_max, usage = grade_group(
                group,
                solution_parsed,
                student_parsed,
                cfg,
                ctx,
                student_name=student_name,
            )
        usage_total = usage_total.merged(usage)
        score_delta, max_delta = _store_group_grades(
            questions,
            feedback_parts,
            group,
            grade_items,
            qid_to_max,
            model=ctx.model,
            rubrics=cfg.rubrics,
        )
        total_score += score_delta
        total_max += max_delta

    if not is_merge:
        _zero_ungrouped_questions(
            questions,
            feedback_parts,
            ungrouped,
            solution_parsed,
            skipped_feedback(grade_only),
        )

    if is_merge:
        total_score, total_max, feedback_parts = compute_totals_from_questions(
            questions
        )

    result = _build_graded_result(
        student_name, questions, total_score, total_max, feedback_parts, usage_total
    )
    ctx.logger.info(
        "Graded %s: %.1f/%.1f (tokens: %d in / %d out)",
        student_name,
        total_score,
        total_max,
        usage_total.prompt_tokens,
        usage_total.completion_tokens,
    )
    return result


# ---------------------------------------------------------------------------
# Post-processing utilities
# ---------------------------------------------------------------------------


def main():
    import argparse
    from batch_grader import (
        grade_all_students,
    )  # local import avoids circular dependency

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to assignment config.yaml (required, typically output/{assignment_name}/config.yaml)",
    )
    args = parser.parse_args()
    if args.config is None:
        parser.error(
            "--config is required and must point to output/{assignment_name}/config.yaml"
        )

    config = load_app_config(args.config)
    for evt in grade_all_students(config):
        if evt["status"] == "done":
            r = evt["result"]
            print(f"✓ {r['student_name']}: {r['total_score']}/{r['total_max']}")
        elif evt["status"] == "usage":
            u = TokenUsage.from_json_dict(evt.get("usage"))
            cost = evt.get("cost_usd", 0)
            print(
                f"Token usage: {u.prompt_tokens:,} in / {u.completion_tokens:,} out — ~${cost:.4f}"
            )
        else:
            print(f"✗ {evt['student']}: {evt['error']}")
    print("Done.")


if __name__ == "__main__":
    main()
