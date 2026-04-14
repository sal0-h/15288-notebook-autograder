# Project Guidelines

## Build and Test

```bash
# Environment setup
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run full test suite (220 tests)
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

This is an LLM-assisted Jupyter notebook grading pipeline. The core flow is:

```
gather → parse → generate-rubrics → grade → calibrate → export
```

**Assignment-scoped output model:** All runtime data lives under `output/{assignment_name}/`. The root `config.yaml` is an example template only — never the runtime source of truth. The active config is always `output/{assignment_name}/config.yaml`.

**Module organization by responsibility:**

| Layer | Modules |
|-------|---------|
| Config & models | `config_models.py` (AppConfig, schema, load/save, paths), `grading_models.py` (all LLM response schemas), `results_models.py` (GradedResult, Question), `token_usage.py` (TokenUsage, pricing, cost) |
| LLM engine | `llm_client.py` (OpenAI client, temperature), `llm/json_runner.py` (structured output, retry, `run_jobs`, `extract_llm_questions`) |
| Pipeline | `parse_notebook.py`, `rubric_generate.py`, `rubric_review.py`, `grade.py`, `batch_grader.py`, `genai_detection.py`, `calibrate.py` |
| Prompt | `prompt_builder.py` (prompt construction, sanitization), `prompts/DEFAULT/*.md` (templates) |
| Export | `export.py` (Gradescope JSON, Excel, autograder ZIP), `linter_export.py` (format linter ZIP), `gradescope_runtime.py` + `gradescope_submitters.py` (Gradescope harness) |
| Web | `app.py` (FastAPI factory), `api/routers/` (route handlers), `api/state.py` (locks, active config) |
| Shared | `utils.py` (logging, filename sanitization), `grading_helpers.py` (grade_only filtering), `zip_helpers.py` (ZIP archive helper) |

**LLM calling pattern:** All four LLM tasks (grading, rubric gen, rubric review, genai detection) use the same pipeline: `execute_llm_task()` → `complete_structured()` (OpenAI Responses API) → `postprocess` callback → optional `fallback_factory` on exhaustion. Shared helpers: `extract_llm_questions()` for QID normalize+dedup+validate, `run_jobs()` for sequential/parallel dispatch.

**Concurrency:** `api/state.py` has three locks (`grading_lock`, `results_lock`, `rubric_lock`). Acquire in consistent order: grading → results → rubric. `batch_grader` uses `ThreadPoolExecutor` for parallel grading with a shared OpenAI client.

## Key Conventions

**Question IDs** are canonical numeric strings (`"1.1"`, `"2.3"`). Always normalize with `normalize_qid()` from `config_models`. LLM postprocess functions use `extract_llm_questions()` which handles this automatically.

**Prompts** live in `prompts/{assignment_name}/` or `prompts/DEFAULT/`, loaded via `load_prompt(name, assignment_name=...)`. Never persist prompt content in config YAML.

**Student content is untrusted.** Prompt injection boundaries (`<<<STUDENT_SUBMISSION>>>`) and `_sanitize_student_text()` must stay intact. Student text that contains `<<<`/`>>>` is replaced with `«`/`»`.

**Incremental save:** `graded_results.json` is written after every student completes, not at the end. This enables resume on crash. `batch_grader` checks existing results at startup and skips already-graded students.

**grade_only / grade_only_merge:** When `grading.grade_only` is set, only those QIDs are re-graded. `GradingConfig.get_effective_groups()` filters question groups. `needs_merge()` in `grading_helpers` determines if a student needs re-grading. Merge mode seeds the result dict from existing results.

**API partial payloads:** UI sends partial config updates to `/config`. Handlers must merge into existing config, never treat missing fields as null.

**Export schema stability:** Gradescope JSON must have `tests` array with `name`, `score`, `max_score`, `output`, `visibility`, optional `output_format`. Do not change this shape.

**Testing:** All LLM calls are mocked via `unittest.mock.patch` on `llm.json_runner.complete_structured`. Tests use `tmp_path` for output isolation. Config dicts in tests use `DEFAULT_MODEL` from `config_models`.

**Documentation discipline:** When changing routes, locks, tests, or public behavior, update `docs/CODEBASE_GUIDE.md` in the same commit. Truth lives in code; docs must not contradict it.
