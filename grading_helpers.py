"""Grading config helpers: grade_only filtering, merge logic, effective groups."""

from __future__ import annotations

from typing import Any

from grading_models import GRADING_FAILED, SKIP_FEEDBACKS


def _config_get(config: dict | Any, key: str, default: Any = None) -> Any:
    if hasattr(config, "model_dump"):
        return getattr(config, key, default)
    return config.get(key, default) if isinstance(config, dict) else default


def filter_groups_by_grade_only(
    groups: list[list[str]], grade_only: list[str] | None
) -> list[list[str]]:
    """Return question groups filtered to only grade_only questions when provided."""
    if not grade_only:
        return groups
    grade_only_set = set(grade_only)
    return [
        [q for q in group if q in grade_only_set]
        for group in groups
        if group and any(q in grade_only_set for q in group)
    ]


def grade_only_list(grading_config: dict | Any) -> list[str] | None:
    """Return grade_only list from grading config, or None."""
    grade_only = _config_get(grading_config, "grade_only")
    return grade_only if grade_only else None


def effective_groups(grading_config: dict | Any) -> list[list[str]]:
    """Return question groups filtered by grade_only when set."""
    groups = _config_get(grading_config, "question_groups", [])
    return filter_groups_by_grade_only(groups, grade_only_list(grading_config))


def is_grade_only_merge_enabled(grading_config: dict | Any) -> bool:
    """True if grade_only_merge is on and grade_only is set."""
    return bool(
        _config_get(grading_config, "grade_only_merge")
        and grade_only_list(grading_config)
    )


def needs_merge(existing_result: dict | None, grade_only: list[str] | None) -> bool:
    """True if we need to (re)grade for grade_only (missing or retryable feedback)."""
    if not grade_only:
        return False
    if not existing_result:
        return True
    questions = existing_result.get("questions", {})
    retryable = {SKIP_FEEDBACKS[0], GRADING_FAILED}
    for qid in set(grade_only):
        if qid not in questions:
            return True
        feedback = (questions[qid].get("feedback") or "").strip()
        if feedback in retryable:
            return True
    return False


def skipped_feedback(grade_only: list[str] | None) -> str:
    """Canonical feedback for skipped questions (grade_only vs not)."""
    return SKIP_FEEDBACKS[0] if grade_only else SKIP_FEEDBACKS[1]


# Backward-compatible aliases
get_active_grade_only = grade_only_list
get_effective_question_groups = effective_groups
needs_grade_only_merge = needs_merge
get_skipped_feedback = skipped_feedback
