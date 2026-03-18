---
description: "Use when editing pipeline modules: parse_notebook.py, rubric.py, grade.py, batch_grader.py, export.py. Covers single-purpose design, shared utilities, assignment-scoped output, incremental save and resume behavior."
name: "Python Pipeline Guidelines"
applyTo: "{**/parse_notebook.py,**/rubric.py,**/grade.py,**/batch_grader.py,**/export.py,**/prompt_builder.py,**/grading_models.py}"
---

# Python Pipeline Guidelines

## Single-Purpose Module Design

Each pipeline module should have one clear responsibility:
- **parse_notebook.py**: Parse solution and student notebooks into question-level JSON.
- **rubric.py**: Generate rubrics from reference solution.
- **grade.py**: Grade a single student/group against a rubric.
- **batch_grader.py**: Orchestrate sequential or parallel grading across students.
- **export.py**: Write Gradescope JSON, Excel, and autograder artifacts.

Keep modules focused. Share behavior across modules via **utils.py** helpers (config I/O, path resolution, filtering, logging).

## Assignment-Scoped Artifacts

All runtime artifacts are stored under `output/{assignment_name}/`:
- `config.yaml` — authoritative runtime config (never root config.yaml)
- `submissions/` — student notebooks
- `parsed/` — parsed JSON per student
- `solution_parsed.json` — parsed reference solution
- `graded_results.json` — incremental grading results
- `autograder.log` — assignment-specific logging

When switching assignments, ensure path resolution and logging rebind to the active assignment. Use `setup_assignment_logging()` helper from utils.py to avoid stale file handlers.

## Incremental Save and Resume

Grading results are written to `graded_results.json` **after each student/group batch**, not only at the end. This enables:
- Resume interrupted runs without re-grading completed entries.
- Partial regrading with `grade_only` + `grade_only_merge` semantics.
- Inspection and independent audit of in-progress results.

When resuming, check `graded_results.json` to determine which students have been processed and only grade the remainder.

## Shared Utilities Pattern

For cross-module behavior, add or update helpers in `utils.py`:
- Config loading and validation (`load_config`, `save_config`).
- Path handling and assignment-scoped resolution (`get_assignment_dir`, `get_output_path`).
- Filtering and grouping (`filter_groups_by_grade_only`, `normalize_qid`).
- Logging setup (`setup_assignment_logging`).

Avoid duplicating logic; consolidate in utils.py so all modules use the same behavior.

## Backward-Compatible API Payloads

Preserve the shape of payloads used by the UI (`ui/js/*.js`) and tests:
- Export JSON structure for Gradescope (`tests` array with `name`, `score`, `max_score`, `output`, `visibility`).
- Config payload layout (do not add required top-level fields without deprecation notice).
- Result JSON schema for incremental saves.

Breaking changes require coordinated updates across UI, tests, and documentation.

## Question ID Canonicalization

Canonical question IDs are numeric strings like `"1.1"`. Normalize any `Q1.1`, `q1.1`, or `1_1` form through `normalize_qid()` helper. Keep this consistent across parsing, rubric generation, and grading.
