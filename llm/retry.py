"""Application-level retry with exponential backoff between attempts."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_with_exponential_backoff(
    operation: Callable[[], T],
    *,
    max_attempts: int,
    on_before_retry: Callable[[int, float], None] | None = None,
    on_attempt_failed: Callable[[int, BaseException], None] | None = None,
) -> tuple[T | None, BaseException | None]:
    """Run ``operation`` up to ``max_attempts`` times.

    Before attempts 1 .. max_attempts-1, sleep ``2**attempt`` seconds (same as
    historical ``grade_group`` behavior). Optionally notify before sleeping and
    after each failure.

    Returns ``(result, None)`` on success, or ``(None, last_error)`` if every
    attempt raises.
    """
    last_error: BaseException | None = None
    for attempt in range(max_attempts):
        if attempt > 0:
            delay = float(2**attempt)
            if on_before_retry is not None:
                on_before_retry(attempt, delay)
            time.sleep(delay)
        try:
            return (operation(), None)
        except BaseException as e:
            last_error = e
            if on_attempt_failed is not None:
                on_attempt_failed(attempt, e)
    return (None, last_error)
