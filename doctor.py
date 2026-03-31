#!/usr/bin/env python3
"""Assignment health check: config, parse artifacts, grade queue, API key.

Usage:
  python doctor.py --config output/<assignment_name>/config.yaml

Loads ``.env`` from the repository root (same as ``get_openai_client``) so ``OPENAI_API_KEY`` is visible.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from batch_grader import load_grade_queue
from config_models import load_app_config
from utils import get_assignment_output_paths


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _err(msg: str) -> None:
    print(f"  [ERR]  {msg}")


def run(config_path: Path) -> int:
    """Return 0 if no errors, 1 if blocking issues."""
    # Match utils.get_openai_client: .env is not applied to os.environ unless loaded.
    repo_root = Path(__file__).resolve().parent
    load_dotenv(repo_root / ".env")

    errors = 0
    print(f"Doctor — config: {config_path.resolve()}")

    if not config_path.is_file():
        _err(f"Config file not found: {config_path}")
        return 1

    try:
        cfg = load_app_config(config_path)
    except Exception as e:
        _err(f"Invalid config: {e}")
        return 1

    _ok(f"assignment_name={cfg.assignment_name!r}")

    paths = get_assignment_output_paths(cfg)
    sol = paths.solution_parsed
    if not sol.exists():
        _err(f"Missing {sol.name} — run Parse (solution) first.")
        errors += 1
    else:
        _ok(f"{sol.name} present")

    parsed_dir = paths.parsed_dir
    if not parsed_dir.is_dir():
        _warn(f"Parsed dir missing or not a directory: {parsed_dir}")
        n_parsed = 0
    else:
        n_parsed = len(list(parsed_dir.glob("*.json")))
        _ok(f"Parsed notebooks: {n_parsed} under {parsed_dir}")

    graded_path = paths.graded_results
    if graded_path.exists():
        try:
            raw_g = json.loads(graded_path.read_text(encoding="utf-8"))
            n_graded = len(raw_g) if isinstance(raw_g, list) else 0
            _ok(f"graded_results.json: {n_graded} row(s)")
        except Exception as e:
            _warn(f"graded_results.json exists but could not read: {e}")
    else:
        _ok("graded_results.json: (none yet)")

    key = os.environ.get("OPENAI_API_KEY")
    if not key and os.environ.get("key"):
        _warn("Using deprecated 'key' in .env — set OPENAI_API_KEY instead.")
        key = os.environ.get("key")
    if not key:
        _warn("OPENAI_API_KEY not set — grading will fail until set.")
    else:
        _ok("OPENAI_API_KEY is set")

    if errors:
        print("\nQueue summary: skipped (fix errors above).")
        return 1

    try:
        gq = load_grade_queue(cfg)
    except FileNotFoundError as e:
        _err(str(e))
        return 1
    except Exception as e:
        _err(f"load_grade_queue failed: {e}")
        return 1

    pending = len(gq.to_grade)
    total = len(gq.student_files)
    skipped = total - pending
    _ok(
        f"Grade queue: {pending} pending, {skipped} skipped (already graded), {total} parsed total"
    )
    if pending == 0 and total > 0:
        _warn(
            "Nothing left to grade — remove or edit graded_results.json to re-grade, or use grade-only merge."
        )
    elif total == 0:
        _warn("No parsed student JSONs — run Parse on submissions.")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check assignment config and pipeline artifacts (non-destructive)."
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Path to output/{assignment}/config.yaml",
    )
    args = parser.parse_args()
    sys.exit(run(args.config))


if __name__ == "__main__":
    main()
