"""Grading config helpers: grade_only filtering, merge logic, effective groups.

Most logic now lives on GradingConfig methods. This module provides free-standing
functions for callers that pass a GradingConfig (or groups + grade_only lists)
rather than calling methods directly.
"""

from __future__ import annotations

from config_models import GradingConfig
from grading_models import GRADING_FAILED, SKIP_FEEDBACKS
from results_models import GradedResult


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


def grade_only_list(grading_config: GradingConfig) -> list[str] | None:
    """Return grade_only list from grading config, or None."""
    return grading_config.get_grade_only()


def effective_groups(grading_config: GradingConfig) -> list[list[str]]:
    """Return question groups filtered by grade_only when set."""
    return grading_config.get_effective_groups()


def needs_merge(
    existing_result: GradedResult | None, grade_only: list[str] | None
) -> bool:
    """True if we need to (re)grade for grade_only (missing or retryable feedback)."""
    if not grade_only:
        return False
    if existing_result is None:
        return True
    retryable = {SKIP_FEEDBACKS[0], GRADING_FAILED}
    for qid in set(grade_only):
        q = existing_result.questions.get(qid)
        if q is None or (q.feedback or "").strip() in retryable:
            return True
    return False


def skipped_feedback(grade_only: list[str] | None) -> str:
    """Canonical feedback for skipped questions (grade_only vs not)."""
    return SKIP_FEEDBACKS[0] if grade_only else SKIP_FEEDBACKS[1]

