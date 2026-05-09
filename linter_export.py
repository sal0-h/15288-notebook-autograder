"""Create a Phase 1 linter autograder zip for Gradescope (pre-deadline format check, 0 pts)."""

import zipfile
from pathlib import Path

from parse_notebook import get_all_question_ids, parse_notebook
from config_models import load_app_config
from zip_helpers import write_to_zip

_TEMPLATE_PATH = Path(__file__).resolve().parent / "linter_run_autograder.py.tpl"

LINTER_SETUP_SH = """#!/bin/bash
# Python 3 is available on Gradescope; no setup required
"""


def _build_run_autograder(required_ids: list[str], question_regex: str) -> str:
    """Generate the run_autograder script from the template with embedded IDs and regex."""
    template = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("{required_ids}", repr(required_ids)).replace(
        "{question_regex}", repr(question_regex)
    )


def fmt_qid_list(items: list[str]) -> str:
    """Format a list of question IDs as a markdown inline list, or '(none)'."""
    if not items:
        return "(none)"
    return ", ".join(f"`{q}`" for q in items)


def build_linter_summary(
    found: list[str],
    required: list[str],
    missing: list[str],
    unexpected: list[str],
    duplicates: list[str],
    notebook_name: str = "",
) -> tuple[str, bool]:
    """
    Build the linter markdown summary string and pass/fail status.
    Returns (summary_str, ok).
    """
    ok = len(missing) == 0 and len(duplicates) == 0
    lines = [
        "# Notebook Linter Summary",
        "",
        f"Status: {'PASSED' if ok else 'FAILED'}",
        "",
    ]
    if notebook_name:
        lines += [f"Notebook: `{notebook_name}`", ""]
    lines += [
        f"Required questions: **{len(required)}**",
        f"Questions found: **{len(found)}**",
        f"Questions missing: **{len(missing)}**",
        f"Duplicate labels: **{len(duplicates)}**",
        f"Unexpected question labels: **{len(unexpected)}**",
        "",
        "## Questions Found",
        fmt_qid_list(found),
        "",
        "## Duplicates Found",
        fmt_qid_list(duplicates),
        "",
        "## Questions Missing",
        fmt_qid_list(missing),
        "",
        "## Unexpected Question Labels",
        fmt_qid_list(unexpected),
    ]
    return "\n".join(lines), ok


def export_linter_zip(config_path: Path | None = None) -> Path:
    """
    Create linter_autograder.zip for pre-deadline format validation.

    Loads config, parses solution to get required Q IDs, embeds them and the
    question_regex in run_autograder, writes the zip with Gradescope-compatible
    Unix file attributes (see ``zip_helpers.write_to_zip``).

    Returns path to the created zip file.
    """
    if config_path is None:
        raise ValueError("config_path is required")
    cfg = load_app_config(config_path)
    output_dir = Path(cfg.output_dir)
    solution_path = Path(cfg.solution_notebook)

    if not solution_path or not solution_path.exists():
        raise FileNotFoundError(
            f"Solution notebook not found: {solution_path}. Set solution_notebook in config."
        )

    solution_parsed = parse_notebook(solution_path, cfg)
    required_ids = get_all_question_ids(solution_parsed)
    if not required_ids:
        raise ValueError("Solution has no questions; cannot build linter.")

    question_regex = cfg.parsing.question_regex

    run_autograder = _build_run_autograder(required_ids, question_regex)
    zip_path = output_dir / "linter_autograder.zip"
    output_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        write_to_zip(zf, "setup.sh", LINTER_SETUP_SH, executable=True)
        write_to_zip(zf, "run_autograder", run_autograder, executable=True)

    return zip_path


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Create linter_autograder.zip for Gradescope pre-deadline format check"
    )
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

    zip_path = export_linter_zip(args.config)
    print(f"Created: {zip_path}")
    print(
        "Upload this zip in Gradescope before the deadline for format validation (0 pts)."
    )
    print("After the deadline, replace with gradescope_autograder.zip from Export.")


if __name__ == "__main__":
    main()
