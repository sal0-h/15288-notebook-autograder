"""Encapsulates read/find/update/write for graded_results.json.

All public functions take and return ``GradedResult`` objects. Callers are
responsible for constructing ``GradedResult`` before calling into this module.
"""

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from results_models import GradedResult

logger = logging.getLogger(__name__)


def load_results_with_backup(
    path: Path, logger_obj: logging.Logger | None = None
) -> list[dict]:
    """Load graded_results.json as raw dicts. On corruption, backup to *.broken and return []."""
    log = logger_obj or logger
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # Use timestamped backup to avoid overwriting previous backups
        ts = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup_path = path.parent / f"{path.name}.broken.{ts}"
        try:
            shutil.copy2(path, backup_path)
            log.error(
                "graded_results.json is corrupted (invalid JSON). "
                "Backed up to %s. Starting fresh.",
                backup_path,
                exc_info=True,
            )
        except OSError:
            log.exception("Failed to backup corrupted graded_results.json")
        return []
    return raw if isinstance(raw, list) else []


def load_results(
    path: Path, logger_obj: logging.Logger | None = None
) -> list[GradedResult]:
    """Load graded_results.json, validating each entry through GradedResult.

    Returns ``[]`` if missing. On invalid JSON, backs up the file and returns ``[]``.
    Skips (with a warning) any entry that does not conform to the schema.
    """
    log = logger_obj or logger
    raw = load_results_with_backup(path, logger_obj=log)
    validated: list[GradedResult] = []
    for entry in raw:
        try:
            validated.append(GradedResult.model_validate(entry))
        except Exception as e:
            log.warning("Skipping invalid graded result: %s", e)
    return validated


def deduplicate_results(results: list[GradedResult]) -> list[GradedResult]:
    """Deduplicate by student_name, keeping last occurrence."""
    seen: dict[str, GradedResult] = {}
    for r in results:
        if r.student_name:
            seen[r.student_name] = r
    return list(seen.values())


def save_results(
    path: Path,
    results: list[GradedResult],
    logger_obj: logging.Logger | None = None,
) -> None:
    """Write results to graded_results.json atomically. Deduplicates by student_name.

    Uses temp-file-then-rename to prevent half-written files on crash.
    """
    log = logger_obj or logger
    path.parent.mkdir(parents=True, exist_ok=True)
    deduped = deduplicate_results(results)
    data = [
        r.model_dump(mode="python", by_alias=True, exclude_none=True) for r in deduped
    ]
    # Atomic write: write to temp file, then rename (atomic on POSIX)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    log.info("Saved %d graded result entries to %s", len(deduped), path)


def update_student(
    results: list[GradedResult],
    student_name: str,
    result: GradedResult,
    logger_obj: logging.Logger | None = None,
) -> None:
    """Update or append a student result in-place. Mutates results."""
    log = logger_obj or logger
    for i, r in enumerate(results):
        if r.student_name == student_name:
            results[i] = result
            log.info("Updated existing graded result for %s", student_name)
            return
    results.append(result)
    log.info("Added new graded result for %s", student_name)


def find_student(results: list[GradedResult], student_name: str) -> GradedResult | None:
    """Return the result for the given student, or None."""
    for r in results:
        if r.student_name == student_name:
            return r
    return None
