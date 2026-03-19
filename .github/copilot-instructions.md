# Project Guidelines

## Code Style
- Follow existing Python style in this repo: clear function boundaries, typed signatures where present, and small focused helpers.
- Keep pipeline modules single-purpose:
  - `parse_notebook.py` parses
  - `rubric/` (`impl.py`) generates rubrics
  - `grade.py` grades per student/group
  - `batch_grader.py` orchestrates batch grading
  - `export.py` exports artifacts
- Prefer updating existing utilities in `utils.py`, `grading_helpers.py` (grade_only filtering), or `config_models.py` (normalize_qid, AppConfig) for shared behavior instead of duplicating logic.
- Preserve backward-compatible API payload shapes used by the UI (`ui/js/*.js`) and tests.

## Architecture
- This project uses an output-first assignment layout.
  - Root `config.yaml` is an example template only and is not the runtime source of truth.
  - Assignment runtime config is stored in `output/{assignment_name}/config.yaml`.
  - Prompts are loaded from `prompts/{assignment}/` or `prompts/DEFAULT/`; prompt content is not persisted in config.
- All runtime artifacts are assignment-scoped under `output/{assignment_name}/`:
  - `submissions/`, `parsed/`, `solution_parsed.json`, `graded_results.json`, `calibration_report.json`, `gradescope/`, `Final_Grades.xlsx`, `autograder.log`.
- Grading flow:
  - `prompt_builder.py` builds prompts and parses LLM JSON
  - `grading_models.py` validates/coerces grading output
  - `grade.py` computes per-student grades
  - `batch_grader.py` handles sequential/parallel grading and resume behavior
- Web app (`app.py`) is the orchestration layer for UI/API with locks for concurrency (`_grading_lock`, `_results_lock`, `_rubric_lock`).

## Build and Test
- Create environment and install dependencies:
  - `python -m venv .venv`
  - `.venv/bin/pip install -r requirements.txt`
- Run API/UI backend:
  - `uvicorn app:app --reload`
- Run default CLI pipeline:
  - `python main.py`
- Run tests:
  - `.venv/bin/python -m pytest tests/ -q`
- Run focused tests while editing:
  - `.venv/bin/python -m pytest tests/test_app.py -q`
  - `.venv/bin/python -m pytest tests/test_grade.py -q`

## Conventions
- Treat student notebook content as untrusted input. Keep prompt-injection mitigations intact (`<<<STUDENT_SUBMISSION>>>` boundaries and sanitizer usage).
- Canonical question IDs are numeric strings like `"1.1"`; normalize any `Q1.1`/`q1.1` forms through existing model/parsing helpers.
- Prompts are filesystem-managed (`prompts/{assignment}/` or `prompts/DEFAULT/`) and should never be persisted in `config.yaml` payloads.
- When `grading.grade_only` is configured, use shared filtering helpers (`filter_groups_by_grade_only` in `grading_helpers.py`, re-exported by utils) and preserve merge semantics (`grade_only_merge`) where applicable.
- Preserve incremental save and resume behavior for grading results (`graded_results.json` written after each student/group batch result).
- Keep logging bound to the active assignment output path (`output/{assignment_name}/autograder.log`) and avoid duplicate stale file handlers.

## Pitfalls
- Do not assume `/config` payloads are complete in API handlers; UI can send partial updates.
- Never add or persist prompt content in runtime config payloads; prompts must remain filesystem-managed.
- Treat `output/{assignment_name}/config.yaml` as the authoritative runtime config source for API reads/writes and job execution.
- Be careful with assignment switching: path resolution and logging must follow active assignment config.
- Keep thread-safety for results updates in API and batch grading paths.
- Avoid breaking exported Gradescope JSON structure (`tests` array entries with `name`, `score`, `max_score`, `output`, `visibility`, optional `output_format`).
