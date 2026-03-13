"""LLM-based per-group and per-student grading logic."""

import json
import logging
import time
from pathlib import Path

from openai import OpenAI

from grading_models import GradingResponse, NO_SUBMISSION, QuestionGrade, SKIP_FEEDBACKS
from prompt_builder import (
    build_group_prompt,
    get_question_data,
    load_prompt,
    parse_llm_json,
    validate_question_groups,
)
from utils import (
    AppConfig,
    ensure_app_config,
    DEFAULT_MODEL,
    get_active_grade_only,
    get_openai_client,
    get_effective_question_groups,
    get_skipped_feedback,
    load_config,
    temperature_for_model,
    get_job_logger,
)

logger = logging.getLogger(__name__)

MAX_VALIDATION_RETRIES = 2  # application-level retries if Pydantic parse fails


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_no_submission_feedback(feedback: str) -> str:
    """If feedback indicates no submission, normalise to the canonical '[no submission]'."""
    if not feedback or not feedback.strip():
        return feedback
    s = feedback.strip().lower()
    if "[no submission]" in s:
        return NO_SUBMISSION
    if "no submission" in s:
        return NO_SUBMISSION
    if "not found in the student submission" in s:
        return NO_SUBMISSION
    if "question was not found" in s:
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


# ---------------------------------------------------------------------------
# Core grading
# ---------------------------------------------------------------------------


def grade_group(
    group: list[str],
    solution_parsed: dict,
    student_parsed: dict,
    config: AppConfig,
    client: OpenAI,
    student_name: str | None = None,
) -> tuple[GradingResponse, dict[str, int], dict]:
    """
    Grade one question group. Retries up to MAX_VALIDATION_RETRIES times
    if the LLM response fails Pydantic validation.
    """
    logger = get_job_logger(config, __name__)
    cfg = config
    model = cfg.model or DEFAULT_MODEL
    assignment_name = cfg.assignment_name
    system_prompt = load_prompt("grade_system", assignment_name=assignment_name)

    max_prompt_tokens = cfg.max_prompt_tokens
    max_completion_tokens = cfg.max_completion_tokens
    effective_max_completion = min(max_completion_tokens, max(2048, len(group) * 1024))

    rubrics = cfg.rubrics
    include_reference = cfg.include_reference_in_grading
    messages, qid_to_max = build_group_prompt(
        group,
        solution_parsed,
        student_parsed,
        system_prompt,
        max_prompt_tokens,
        model,
        rubrics=rubrics,
        include_reference=include_reference,
    )

    ctx = f" [{student_name}]" if student_name else ""
    last_error: Exception | None = None
    for attempt in range(MAX_VALIDATION_RETRIES + 1):
        if attempt > 0:
            wait = 2**attempt
            logger.warning(
                "Retry %d for group %s%s after %ds", attempt, group, ctx, wait
            )
            time.sleep(wait)

        temperature = temperature_for_model(model)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_completion_tokens=effective_max_completion,
            response_format={"type": "json_object"},
        )
        raw_content = response.choices[0].message.content
        content = raw_content or "{}"
        raw = parse_llm_json(content)
        usage = {}
        if getattr(response, "usage", None):
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(response.usage, "completion_tokens", 0)
                or 0,
            }

        try:
            grading_response = GradingResponse.from_raw(raw, group)
            # Retry if LLM returned any placeholder (partial response = missing data)
            placeholder_feedback = (
                "[not returned by LLM]",
                "[parse error in LLM response]",
            )
            missing = [
                qid
                for qid, g in grading_response.grades.items()
                if g.feedback.strip() in placeholder_feedback
            ]
            total_max = sum(qid_to_max.get(q, 0) for q in group)
            if total_max > 0 and missing:
                reasons = {
                    qid: grading_response.grades[qid].feedback.strip()
                    for qid in missing
                }
                raise ValueError(
                    f"LLM returned partial or malformed response — missing/invalid for: {missing} "
                    f"({reasons})"
                )
            return grading_response, qid_to_max, usage
        except Exception as e:
            last_error = e
            logger.warning(
                "Validation failed on attempt %d for group %s%s: %s",
                attempt,
                group,
                ctx,
                e,
            )
            finish_reason = getattr(response.choices[0], "finish_reason", "?")
            if raw_content:
                preview = raw_content[:600] + ("..." if len(raw_content) > 600 else "")
                logger.info(
                    "LLM response (len=%d, finish_reason=%s): %r",
                    len(raw_content),
                    finish_reason,
                    preview,
                )
            else:
                logger.info("LLM returned None/empty. finish_reason=%s", finish_reason)

    # All retries exhausted — return zeros
    logger.error(
        "Giving up on group %s%s after %d attempts: %s",
        group,
        ctx,
        MAX_VALIDATION_RETRIES + 1,
        last_error,
    )
    return (
        GradingResponse(
            grades={
                qid: QuestionGrade(
                    score=0.0,
                    feedback="[grading failed after retries]",
                    confidence="low",
                    requires_review=True,
                )
                for qid in group
            }
        ),
        qid_to_max,
        {},
    )


def grade_student(
    student_parsed: dict,
    solution_parsed: dict,
    config: AppConfig | dict,
    client: OpenAI | None = None,
    ungrouped: list[str] | None = None,
    merge_into: dict | None = None,
) -> dict:
    """Grade one student. Returns result dict for graded_results.json.

    When merge_into is provided with grade_only, only grades grade_only questions
    and merges new grades into existing result (keeps other questions unchanged).
    """
    logger = get_job_logger(config, __name__)
    cfg = ensure_app_config(config)
    if client is None:
        client = get_openai_client()

    grading_config = cfg.grading
    groups = get_effective_question_groups(grading_config)
    grade_only = get_active_grade_only(grading_config)
    student_name = student_parsed.get("student_name", "Unknown")
    is_merge = merge_into is not None and grade_only is not None

    if ungrouped is None:
        ungrouped = validate_question_groups(groups, solution_parsed)

    questions: dict[str, dict] = {}
    if is_merge:
        questions = dict(merge_into.get("questions", {}))
    total_score = 0.0
    total_max = 0.0
    feedback_parts: list[str] = []
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    logger.info("Grading %s (%d groups)", student_name, len(groups))
    for group_idx, group in enumerate(groups):
        if not group:
            continue
        logger.info(
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
            logger.info(
                "Skipping group %s for %s (all questions missing)",
                group,
                student_name,
            )
            qid_to_max = {}
            for qid in group:
                sol_q = get_question_data(solution_parsed, qid)
                qid_to_max[qid] = (sol_q or {}).get("points", 0)
            grades = {
                qid: QuestionGrade(score=0.0, feedback="[no submission]")
                for qid in group
            }
            grading_response = GradingResponse(grades=grades)
            usage = {}
        else:
            grading_response, qid_to_max, usage = grade_group(
                group,
                solution_parsed,
                student_parsed,
                cfg,
                client,
                student_name=student_name,
            )
        usage_total["prompt_tokens"] += usage.get("prompt_tokens", 0)
        usage_total["completion_tokens"] += usage.get("completion_tokens", 0)

        for qid in group:
            max_pts = qid_to_max.get(qid, 0)
            total_max += max_pts

            q_grade = grading_response.grades.get(
                qid, QuestionGrade(score=0.0, feedback="[missing]")
            )
            score = max(0.0, min(float(max_pts), q_grade.score))
            feedback = _normalize_no_submission_feedback(q_grade.feedback.strip())

            questions[qid] = {
                "score": score,
                "max": max_pts,
                "feedback": feedback,
                "confidence": q_grade.confidence,
                "requires_review": q_grade.requires_review,
            }
            total_score += score

            # Only include deductions in summary (skip full marks)
            if feedback and score < max_pts:
                feedback_parts.append(f"Q{qid}: {feedback}")

    # Zero-score ungrouped or skipped questions (skip when merging — already in existing)
    if not is_merge:
        skip_msg = get_skipped_feedback(grade_only)
        for qid in ungrouped:
            sol_q = get_question_data(solution_parsed, qid)
            max_pts = (sol_q or {}).get("points", 0)
            # Skipped questions never contribute to total (only graded questions count)
            questions[qid] = {
                "score": 0.0,
                "max": max_pts,
                "feedback": skip_msg,
                "confidence": "low",
                "requires_review": False,
            }
            feedback_parts.append(f"Q{qid}: {skip_msg}")

    if is_merge:
        total_score, total_max, feedback_parts = compute_totals_from_questions(
            questions
        )

    result = {
        "student_name": student_name,
        "questions": questions,
        "total_score": round(total_score, 2),
        "total_max": round(total_max, 2),
        "summary_feedback": (
            ". ".join(feedback_parts) if feedback_parts else "Full marks."
        ),
    }
    if usage_total["prompt_tokens"] or usage_total["completion_tokens"]:
        result["_usage"] = usage_total
    logger.info(
        "Graded %s: %.1f/%.1f (tokens: %d in / %d out)",
        student_name,
        total_score,
        total_max,
        usage_total.get("prompt_tokens", 0),
        usage_total.get("completion_tokens", 0),
    )
    return result


# ---------------------------------------------------------------------------
# Post-processing utilities
# ---------------------------------------------------------------------------


def cleanup_graded_results(path: Path) -> int:
    """Normalize [no submission] feedback in graded_results.json. Returns count of cleaned entries."""
    data = json.loads(path.read_text())
    count = 0
    for result in data:
        for qid, q in result.get("questions", {}).items():
            fb = q.get("feedback", "")
            normalized = _normalize_no_submission_feedback(fb)
            if normalized != fb:
                q["feedback"] = normalized
                count += 1
    path.write_text(json.dumps(data, indent=2))
    return count


def fix_graded_results_totals(path: Path, config: dict | None = None) -> int:
    """Recompute total_score and total_max from graded questions only (exclude skipped). Returns count fixed."""
    data = json.loads(path.read_text())
    count = 0
    for result in data:
        qs = result.get("questions", {})
        if not qs:
            continue
        total_score, total_max, _ = compute_totals_from_questions(qs)
        if (
            abs(result.get("total_score", 0) - total_score) > 0.01
            or abs(result.get("total_max", 0) - total_max) > 0.01
        ):
            result["total_score"] = round(total_score, 2)
            result["total_max"] = round(total_max, 2)
            count += 1
    if count:
        path.write_text(json.dumps(data, indent=2))
    return count


def main():
    import argparse
    from batch_grader import (
        grade_all_students,
    )  # local import avoids circular dependency

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()

    config = load_config(args.config)
    for evt in grade_all_students(config):
        if evt["status"] == "done":
            r = evt["result"]
            print(f"✓ {r['student_name']}: {r['total_score']}/{r['total_max']}")
        elif evt["status"] == "usage":
            u = evt.get("usage", {})
            cost = evt.get("cost_usd", 0)
            print(
                f"Token usage: {u.get('prompt_tokens', 0):,} in / {u.get('completion_tokens', 0):,} out — ~${cost:.4f}"
            )
        else:
            print(f"✗ {evt['student']}: {evt['error']}")
    print("Done.")


if __name__ == "__main__":
    main()
