"""Thread-pool helpers for unordered parallel work (rubrics, batch grading)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

_T = TypeVar("_T")
_R = TypeVar("_R")


def iter_unordered_parallel_results(
    items: Iterable[_T],
    worker: Callable[[_T], _R],
    *,
    max_workers: int,
) -> Iterator[_R]:
    """Run ``worker(item)`` for each item; yield results as tasks finish (arbitrary order)."""
    work = list(items)
    if not work:
        return
    workers = max(1, int(max_workers))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, item): item for item in work}
        for fut in as_completed(futures):
            yield fut.result()
