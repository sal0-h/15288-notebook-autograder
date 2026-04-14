# Project Guidelines

## Agent Rules

- Never add `Co-authored-by` trailers to commit messages. Commits are authored by the developer only.
- Never use the phrase "god module", "god object", or "god class". Use "overloaded module", "central module", or "high-coupling module" instead.
- Never create database files, temp files, or artifacts in the repository root. If a tool creates `.db` or `.sqlite3` files, delete them immediately.
- When moving functions between modules, do NOT add backward-compatible re-exports. Update all callers to import from the new canonical location directly. Grep the codebase to find all import sites before moving.
- All prompt and config file loading must follow the assignment-first fallback pattern: check `prompts/{assignment_name}/` first, fall back to `prompts/DEFAULT/`. Never hardcode DEFAULT as the only lookup path.
- After any code change, verify these docs are still accurate before committing: `docs/CODEBASE_GUIDE.md` (module tree, function names, test table, API endpoints), `README.md` (API endpoint list, module descriptions), `.github/copilot-instructions.md` (module table, test count).

## Build and Test

```bash
# Environment setup
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

# Run full test suite (244 tests)
.venv/bin/python -m pytest tests/ -q

# Run a single test file
.venv/bin/python -m pytest tests/test_grade.py -q

# Run a single test function
.venv/bin/python -m pytest tests/test_grade.py::test_grade_student_merge -q

# Pre-commit checks (formatting + tests)
pre-commit run --all-files

# Run the web UI
uvicorn app:app --reload

# Run CLI pipeline
python main.py --config output/{assignment_name}/config.yaml --steps parse grade export
```

## Architecture

LLM-assisted Jupyter notebook grading pipeline. Core flow:

```
gather → parse → generate-rubrics → grade → calibrate → export
```

**Assignment-scoped output model:** All runtime data lives under `output/{assignment_name}/`. The root `config.yaml` is an example template only. The active config is always `output/{assignment_name}/config.yaml`.

**Module organization by responsibility:**

| Layer | Modules |
|-------|---------|
| Config & models | `config_models.py` (AppConfig, schema, load/save, paths, `load_solution_parsed`), `grading_models.py` (all LLM response schemas), `results_models.py` (GradedResult, Question), `token_usage.py` (TokenUsage, pricing, cost) |
| LLM engine | `llm_client.py` (OpenAI client, temperature), `llm/json_runner.py` (structured output, retry, `run_jobs`, `extract_llm_questions`) |
| Pipeline | `parse_notebook.py`, `rubric_generate.py`, `rubric_review.py`, `grade.py` (returns `GradedResult`), `batch_grader.py`, `genai_detection.py`, `calibrate.py` |
| Prompt | `prompt_builder.py` (prompt construction, sanitization, question type injection), `prompts/DEFAULT/*.md` (templates), `prompts/DEFAULT/question_types.yaml` (per-type grading instructions) |
| Export | `export.py` (Gradescope JSON, Excel, autograder ZIP), `linter_export.py` + `linter_run_autograder.py.tpl` (format linter ZIP), `gradescope_runtime.py` + `gradescope_submitters.py` (Gradescope harness), `zip_helpers.py` |
| Web | `app.py` (FastAPI factory), `api/routers/` (route handlers), `api/state.py` (locks, config cache) |
| Shared | `utils.py` (logging, filename sanitization), `grading_helpers.py` (grade_only filtering) |
| Tools | `tag_notebook.py` (inject question type tags into notebook cells) |

**LLM calling pattern:** All four LLM tasks (grading, rubric gen, rubric review, genai detection) use: `execute_llm_task()` → `complete_structured()` (OpenAI Responses API) → `postprocess` callback → optional `fallback_factory` on exhaustion. Shared helpers: `extract_llm_questions()` for QID normalize+dedup+validate, `run_jobs()` for sequential/parallel dispatch.

**Question type tags:** Notebook cells can have `type:code`, `type:analysis`, `type:plot`, `type:open-ended`, `type:exact` in cell metadata tags. Parser extracts these into `question_type` field. `prompt_builder` and `rubric_generate` inject type-specific grading/rubric instructions from `question_types.yaml`.

**Config caching:** `api/state.py` caches loaded `AppConfig`. Call `invalidate_config_cache()` after any config save operation.

**Concurrency:** `api/state.py` has three locks (`grading_lock`, `results_lock`, `rubric_lock`). Acquire in consistent order: grading → results → rubric.

## Key Conventions

**Question IDs** are canonical numeric strings (`"1.1"`, `"2.3"`). Normalize with `normalize_qid()` from `config_models`. LLM postprocess functions use `extract_llm_questions()` which handles this automatically.

**Prompts** live in `prompts/{assignment_name}/` or `prompts/DEFAULT/`, loaded via `load_prompt(name, assignment_name=...)`. Never persist prompt content in config YAML.

**Student content is untrusted.** Prompt injection boundaries (`<<<STUDENT_SUBMISSION>>>`) and `_sanitize_student_text()` must stay intact.

**`grade_student()` returns `GradedResult`**, not a dict. Callers should not re-validate. Usage is extracted from `result.usage` directly.

**Incremental save:** `graded_results.json` is written atomically (temp+rename) after every student. `batch_grader` skips already-graded students on resume.

**Rubric compliance:** The grading prompt requires the LLM to address every rubric criterion in its feedback. The `score` field is the **final score (points earned)**, not the deduction amount.

**API partial payloads:** UI sends partial config updates to `/config`. Handlers merge into existing config, never treat missing fields as null.

**Export schema stability:** Gradescope JSON must have `tests` array with `name`, `score`, `max_score`, `output`, `visibility`, optional `output_format`.

**Testing:** All LLM calls are mocked via `unittest.mock.patch` on `llm.json_runner.complete_structured`. Tests use `tmp_path` for output isolation. Shared fixtures in `conftest.py`: `sample_config`, `sample_app_config`, `sample_parsed_notebook`, `mock_openai_client`.

**Documentation discipline:** When changing routes, locks, tests, or public behavior, update `docs/CODEBASE_GUIDE.md` in the same commit.
