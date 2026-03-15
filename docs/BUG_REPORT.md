# Engineering Issues Report — AI Autograder

This report tracks current engineering issues across:
- Bugs
- Fragility / maintainability concerns
- Inconsistencies

Status values:
- `Open`: should be fixed in code
- `Accepted`: known design trade-off for now
- `Resolved`: already fixed

## 1) Bugs

| ID | Severity | File | Status | Summary |
|----|----------|------|--------|---------|
No open bug items currently in this section.

## 2) Fragility and Simplification Opportunities

| ID | Severity | File | Status | Summary |
|----|----------|------|--------|---------|
| E-102 | Low | `app.py:321` | Accepted | Solution notebook upload path is outside `output/{assignment}` tree by design |
| E-104 | Low | `batch_grader.py:293`, `batch_grader.py:248` | Open | Parallel grading shares one OpenAI client object across worker threads |

### E-102 — Solution notebook stored outside assignment output tree

`/parse-solution-upload` stores to `{project_root}/{assignment_name}/{assignment_name}_sol.ipynb`.

Impact:
- Mixed artifact layout can confuse operators.

Reason accepted:
- Current behavior is intentional and functional; docs now explicitly mention this.

### E-104 — Shared client object in parallel grading

`grade_all_students` creates one OpenAI client and passes it into all worker calls.

Impact:
- Potential thread-safety/performance uncertainty.

Recommended fix:
- Initialize one client per worker task (or document client thread-safety assumption).

## 3) Inconsistencies

| ID | Severity | Area | Status | Summary |
|----|----------|------|--------|---------|
| E-201 | Medium | Docs vs implementation | Resolved | README and CODEBASE_GUIDE were updated to match assignment-scoped config behavior and prompt handling |

## 4) Recently Resolved Items

| ID | Status | Note |
|----|--------|------|
| B-01 | Resolved | `from __future__ import annotations` added in `utils.py` |
| B-02 | Resolved | Root `config.yaml` indentation fixed |
| B-03 | Resolved | `app.py` imports organized at top |
| B-04 | Resolved | `_setup_file_logging` docstring corrected |
| B-05 | Resolved | Dead `prompts` filtering removed from `save_config` |
| B-09 | Resolved | Duplicate `_apply_config_defaults` call removed |
| B-10 | Resolved | Rubric generation now checks `solution_parsed.json` before API client creation |
| E-001 | Resolved | Standalone pipeline CLIs now require explicit `--config` assignment path instead of defaulting to root `config.yaml` |
| E-002 | Resolved | `api_grade_one` strips `_usage` before persisting to `graded_results.json` |
| E-004 | Resolved | Missing assignment config now raises by default via `load_config(..., require_exists=True)` |
| E-101 | Resolved | Embedded `RUN_AUTOGRADER` now uses exact normalized-name matching and rejects ambiguous matches |
| E-202 | Resolved | Batch and single-student persisted result schema is now consistent (`_usage` removed before write) |
| E-003 | Resolved | `/gather` now enforces upload size while streaming chunks and aborts before full buffering |
| E-103 | Resolved | `PUT /config` no longer swallows active-config load failures; malformed active config now returns 500 |
