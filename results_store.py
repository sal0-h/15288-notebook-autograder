"""Encapsulates read/find/update/write for graded_results.json.

Used by app.py and batch_grader to avoid duplicating the results-update pattern.
Callers hold the appropriate lock when invoking these functions.
"""

import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def load_results_with_backup(
    path: Path, logger_obj: logging.Logger | None = None
) -> list[dict]:
    """Load graded_results.json. On corruption, backup to *.broken and return []."""
    log = logger_obj or logger
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, KeyError):
        backup_path = path.parent / f"{path.name}.broken"
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


def load_results(path: Path) -> list[dict]:
    """Load graded_results.json. Returns [] if missing or empty."""
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise ValueError(f"graded_results.json is corrupted at {path}")
    return raw if isinstance(raw, list) else []


def deduplicate_results(results: list[dict]) -> list[dict]:
    """Deduplicate by student_name, keeping last occurrence."""
    seen: dict[str, dict] = {}
    for r in results:
        name = r.get("student_name") if isinstance(r, dict) else None
        if name:
            seen[name] = r
    return list(seen.values())


def save_results(
    path: Path,
    results: list[dict],
    logger_obj: logging.Logger | None = None,
) -> None:
    """Write results to graded_results.json. Deduplicates by student_name (keeps last)."""
    log = logger_obj or logger
    path.parent.mkdir(parents=True, exist_ok=True)
    deduped = deduplicate_results(results)
    path.write_text(json.dumps(deduped, indent=2), encoding="utf-8")
    log.info("Saved %d graded result entries to %s", len(deduped), path)


def update_student(
    results: list[dict],
    student_name: str,
    result: dict,
    logger_obj: logging.Logger | None = None,
) -> None:
    """Update or append a student result in-place. Mutates results."""
    log = logger_obj or logger
    for i, r in enumerate(results):
        if isinstance(r, dict) and r.get("student_name") == student_name:
            results[i] = result
            log.info("Updated existing graded result for %s", student_name)
            return
    results.append(result)
    log.info("Added new graded result for %s", student_name)


def find_student(results: list[dict], student_name: str) -> dict | None:
    """Return the first result dict for the given student, or None."""
    for r in results:
        if isinstance(r, dict) and r.get("student_name") == student_name:
            return r
    return None
