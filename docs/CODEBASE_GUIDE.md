# Codebase Guide

A deeper technical reference than the README. Start here if you want to understand
how the pieces fit together, trace data through the pipeline, or make non-trivial changes.

---

## Table of contents

1. [Repository orientation](#1-repository-orientation)
2. [Configuration system](#2-configuration-system)
3. [Data model — what flows between stages](#3-data-model--what-flows-between-stages)
4. [Pipeline stage reference](#4-pipeline-stage-reference)
   - [gather](#41-gather)
   - [parse](#42-parse)
   - [generate-rubrics](#43-generate-rubrics)
   - [grade](#44-grade)
   - [calibrate](#45-calibrate)
   - [export](#46-export)
5. [Prompt system](#5-prompt-system)
6. [Grading engine internals](#6-grading-engine-internals)
7. [Web app and API layer](#7-web-app-and-api-layer)
8. [Security guardrails](#8-security-guardrails)
9. [Testing strategy](#9-testing-strategy)
10. [Design principles and conventions](#10-design-principles-and-conventions)
11. [Common extension scenarios](#11-common-extension-scenarios)

---

## 1. Repository orientation

```
ai_autograder/
├── app.py                  FastAPI backend + static UI serving
├── batch_grader.py         Batch orchestration (sequential + parallel, resume)
├── calibrate.py            Post-grading z-score outlier detection
├── estimate.py             Token and cost estimation
├── export.py               Gradescope JSON, Excel, autograder ZIP
├── gather.py               Submission extraction from Gradescope export
├── grade.py                Per-student and per-group grading logic
├── grading_models.py       Pydantic schemas for LLM response validation
├── linter_export.py        Pre-deadline format linter autograder
├── main.py                 CLI entry point
├── parse_notebook.py       Notebook → structured JSON parser
├── prompt_builder.py       Prompt construction, sanitization, JSON extraction
├── rubric.py               Rubric generation and review
├── utils.py                Config I/O, AppConfig schema, logging, OpenAI client
│
├── prompts/
│   ├── DEFAULT/            Fallback prompt templates (*.md)
│   └── {assignment_name}/  Assignment-specific overrides (*.md)
│
├── output/{assignment_name}/
│   ├── config.yaml         Full runtime config for this assignment
│   ├── submissions/        Gathered student notebooks
│   ├── parsed/             Per-student parsed JSON files
│   ├── solution_parsed.json
│   ├── graded_results.json
│   ├── calibration_report.json
│   ├── gradescope/         Per-student Gradescope JSON
│   ├── Final_Grades.xlsx
│   └── autograder.log
│
├── tests/                  pytest unit + integration tests
└── ui/                     Browser-based interface (HTML/CSS/JS, no build step)
```

### Module roles at a glance

| Module | Responsibility | Calls into |
|---|---|---|
| `main.py` | CLI, config write, pipeline orchestration | All pipeline modules |
| `app.py` | FastAPI routes and SSE events | All pipeline modules |
| `utils.py` | Config load/save, `AppConfig` schema, logging, OpenAI client | nothing (leaf) |
| `grading_models.py` | LLM response Pydantic schemas, constants | nothing (leaf) |
| `prompt_builder.py` | Prompt loading, token counting, sanitization, JSON parsing | `utils` |
| `parse_notebook.py` | Notebook → parsed JSON | `utils` |
| `grade.py` | `grade_group`, `grade_student`, post-processing | `prompt_builder`, `grading_models`, `utils` |
| `batch_grader.py` | Sequential/parallel grading, resume logic | `grade`, `utils` |
| `rubric.py` | Rubric generation + review | `prompt_builder`, `utils` |
| `gather.py` | Submission extraction | `utils` |
| `export.py` | Excel + Gradescope export | `utils` |
| `calibrate.py` | Outlier detection | `utils` |
| `estimate.py` | Cost projection | `prompt_builder`, `utils` |
| `linter_export.py` | Format linter zip | `parse_notebook`, `utils` |

---

## 2. Configuration system

### Assignment-first layout

The project now uses an explicit per-assignment config as the only runtime source
of truth.

**Root `config.yaml`** (project root, optional example only):
```yaml
assignment_name: LabTest_3_S26
output_dir: output
```
This file is only a convenient template/example for humans. The CLI and web app do
not use it as an active pointer.

**Assignment `output/{assignment_name}/config.yaml`** (runtime artifact):
Contains everything else — model choice, tokenizer budgets, rubrics, question groups,
parsing regexes, etc. This is written by the pipeline and should not be hand-edited
while grading is running.

### Loading logic (`utils.load_config`)

`load_config(config_path: Path)` requires an explicit path to the assignment config.

```
config_path must point to output/{assignment_name}/config.yaml
  → load that file directly as the assignment config
  → infer project_root as config_path.parent.parent.parent
  → resolve relative paths against project_root
  → normalize derived paths like output_dir, submissions_dir, parsed_dir
```

After resolution, relative paths (`solution_notebook`, `submissions_dir`, etc.) are
resolved against the project root. Defaults are filled in via `_apply_config_defaults`.

The returned dict is validated through `AppConfig.model_validate` to catch schema
errors early, then returned as a plain dict for backward-compatible consumption.

### `AppConfig` schema (`utils.py`)

All config access inside the pipeline should go through the `AppConfig` Pydantic model:

```python
class AppConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")   # unknown keys silently dropped

    assignment_name: str = "default"
    model: str = DEFAULT_MODEL                  # grading model
    rubric_model: str = ""                      # rubric model; falls back to model
    rubric_review: bool = True
    include_reference_in_grading: bool = False
    solution_notebook: str = ""
    submissions_dir: str = "output/submissions"
    parsed_dir: str = "output/parsed"
    output_dir: str = "output"
    workers: int = 1
    rubrics: dict[str, RubricEntry] = {}
    max_prompt_tokens: int = 80_000
    max_completion_tokens: int = 4_096
    gradescope_title_mapping: dict[str, str] = {}
    parsing: ParsingConfig = ...               # section/question regexes, keep_images
    grading: GradingConfig = ...               # question_groups, grade_only, grade_only_merge
```

Sub-models:

```python
class ParsingConfig(BaseModel):
    section_regex: str      # identifies section headers in notebooks
    question_regex: str     # identifies question IDs and point values
    keep_images: bool = True

class GradingConfig(BaseModel):
    question_groups: list[list[str]] = []   # each inner list = one LLM call
    grade_only: list[str] | None = None     # if set, only these QIDs are graded
    grade_only_merge: bool = False          # merge regraded QIDs into existing results

class RubricEntry(BaseModel):
    points: int
    items: list[RubricItem]                 # deductions must sum to points

class RubricItem(BaseModel):
    description: str
    deduction: float                        # positive; means "-N points"
```

### Canonical coercion idiom

At every `AppConfig | dict` boundary, use `ensure_app_config`:

```python
cfg = ensure_app_config(config)   # no-op if already AppConfig; validates if dict
```

Never call `AppConfig.model_validate(config)` directly in pipeline code — it would
re-validate an already-typed object, wasting work.

### Saving config

`save_config(config: dict | AppConfig, config_path: Path)` writes one file:

1. The assignment config to `output/{assignment_name}/config.yaml` — the full config
  including `rubric_review` and `include_reference_in_grading`.

`solution_notebook` is relativized against the project root before saving so configs
remain portable across machines.

---

## 3. Data model — what flows between stages

### Parsed notebook format

Both the solution and each student notebook are converted to the same structure by
`parse_notebook.py`:

```json
{
  "source_file": "path/to/notebook.ipynb",
  "student_name": "Alice Smith",          // absent for solution
  "sections": {
    "1": {
      "questions": {
        "1.1": {
          "points": 5,
          "question_markdown": "...",      // cell(s) containing the question text
          "answer_code_concat": "...",     // all code cells after this question header
          "answer_text_concat": "...",     // code output text
          "answer_markdown_concat": "...", // markdown answer cells
            "answer_cells": [               // raw cell list for detail access
              {
                "code": "...",
                "output_text": "...",
                "images": [
                  { "mime": "image/png", "base64": "..." }
                ]
              }
            ]
        }
      }
    }
  }
}
```

Question IDs are canonical numeric strings like `"1.1"`. The parser normalizes
`Q1.1` and `q1.1` forms to `"1.1"` everywhere.

### Graded results format (`graded_results.json`)

An array of per-student result objects:

```json
[
  {
    "student_name": "Alice Smith",
    "total_score": 18.5,
    "total_max": 20.0,
    "summary_feedback": "Q2.1: partial credit for ...",
    "questions": {
      "1.1": {
        "score": 5.0,
        "max": 5,
        "feedback": "Correct.",
        "confidence": "high",
        "requires_review": false
      },
      "1.2": {
        "score": 0.0,
        "max": 5,
        "feedback": "[skipped - not in grade_only]",
        "confidence": "low",
        "requires_review": false
      }
    }
  }
]
```

`total_score` and `total_max` only count questions with non-skip feedback.
Questions with `[skipped - not in grade_only]` or `[not included in grading groups]`
are excluded from totals.

### Rubric format (`config.rubrics`)

Rubrics live inside `output/{assignment_name}/config.yaml` under the `rubrics` key:

```yaml
rubrics:
  "1.1":
    points: 5
    items:
      - description: "Missing boundary check"
        deduction: 2.0
      - description: "Incorrect return type"
        deduction: 3.0
```

The deductions for all items in a rubric entry must sum exactly to `points`.
This is enforced by `RubricEntry.validate_deductions_sum`.

---

## 4. Pipeline stage reference

### 4.1 Gather

**Module:** `gather.py`  
**Entry:** `gather_submissions(source, out_dir, *, from_zip)`

Reads Gradescope's `submission_metadata.yml` (Ruby-style `:key` or plain `key`),
resolves student names, and copies each student's notebook into `submissions/` with
the student name as the filename stem (e.g. `Alice Smith.ipynb`).

Handles:
- ZIP input (extracts first, then processes)
- `assignment_*_export` subdirectory layout from Gradescope
- Missing and duplicate submissions (returned in result dicts with appropriate status)

Does **not** modify `config.yaml`.

---

### 4.2 Parse

**Module:** `parse_notebook.py`  
**Key functions:** `parse_notebook(nb_path, config)`, `parse_all_students(config)`

`parse_notebook` walks cells in order:
1. A markdown cell matching `section_regex` opens a new section.
2. A markdown cell matching `question_regex` opens a new question within the current section.
3. Subsequent code cells are accumulated as answer cells; their output text and
   images are collected.
4. Markdown cells after a question header are accumulated as markdown answers.

`parse_all_students` parses the solution notebook (writes `solution_parsed.json`),
then parses every `.ipynb` in `submissions_dir` (writes one `.json` per student to
`parsed_dir`).

**Important:** Images are preserved as base64 payloads in the parsed output when
`parsing.keep_images = true`. These are included in grading prompts as vision
message parts, which increases token usage but enables grading of plots.

---

### 4.3 Generate rubrics

**Module:** `rubric.py`  
**Key function:** `generate_rubrics(config, client, progress_callback, group_indices)`

Flow:
1. Load `solution_parsed.json` from `output_dir`.
2. For each question group (or the subset specified by `group_indices`), call the LLM
   with the solution content and `rubric_system` prompt.
3. Parse the returned JSON into a `{qid: {points, items: [{description, deduction}]}}` dict.
4. If `rubric_review` is enabled, run a second LLM pass (`review_system` prompt) per group
   that softens rubric criteria that hardcode reference-solution-specific values
   not required by the question text.
5. If `group_indices` is set (partial regeneration), merge new rubrics into the
   existing rubric dict rather than replacing everything.

**Rubric review** is a safety pass. It does *not* change point values or deduction
amounts — only description text. The review pass reverts any group where deductions
no longer sum to points after the LLM's rewrite.

Workers (`cfg.workers`) control parallelism via `ThreadPoolExecutor`.

---

### 4.4 Grade

**Modules:** `grade.py`, `batch_grader.py`

#### Per-group (`grade_group`)

One LLM call per question group. Flow:

1. Build prompt via `build_group_prompt` (see [Prompt system](#5-prompt-system)).
2. Call `client.chat.completions.create` with `response_format={"type": "json_object"}`.
3. Parse the response with `parse_llm_json` (tolerates markdown fences).
4. Validate with `GradingResponse.from_raw` which normalizes `Q1.1` and `1.1` key forms.
5. If any QID has a placeholder feedback (`[not returned by LLM]` or
   `[parse error in LLM response]`), treat the response as a validation failure.
6. Retry up to `MAX_VALIDATION_RETRIES = 2` times with exponential backoff (`2^attempt` seconds).
7. On exhaustion, return zero scores with `[grading failed after retries]` feedback.

`effective_max_completion` is capped at `max(2048, len(group) * 1024)` to avoid
burning completion budget on single-question groups.

#### Per-student (`grade_student`)

Iterates over all groups from `get_effective_question_groups(grading_config)`:

- Groups where all questions are absent from the student's parsed output are skipped
  with `[no submission]` scores (no LLM call).
- Questions in `grade_only` that are not in the current group are assigned
  `[skipped - not in grade_only]` and excluded from totals.
- If `merge_into` is provided (from `grade_only_merge` mode), the result dict is
  seeded from existing results before the grading loop, so ungraded questions
  retain their previous scores.

The result dict includes `_usage` with token counts; the batch grader pops this
before writing to `graded_results.json`.

#### Batch orchestration (`batch_grader.grade_all_students`)

Yields progress event dicts for SSE streaming to the UI:
- `{"status": "done", "student": name, "result": {...}}` per student
- `{"status": "error", "student": name, "error": "..."}` on failure
- `{"status": "usage", "usage": {...}, "cost_usd": N}` at the end

**Resume behavior:** Reads existing `graded_results.json` at startup. Students already
in the file (and not in a retryable state) are skipped. The file is incrementally
updated after each student.

**Parallel mode:** When `workers > 1`, uses `ThreadPoolExecutor`. The current
implementation shares the same OpenAI client object passed into `grade_all_students`
across worker threads.

#### `grade_only_merge` flow

When `grade_only` + `grade_only_merge` are both set:
1. `needs_grade_only_merge(existing, grade_only)` checks whether the specified
   QIDs in existing results have retryable feedback. Returns `True` if any QID
   is missing or has `[skipped - not in grade_only]` / `[grading failed after retries]`.
2. If merge is needed, `grade_student` is called with `merge_into=existing_result`.
3. After grading, `compute_totals_from_questions` recomputes `total_score` and
   `total_max` across the merged question set (excluding skip feedbacks).

---

### 4.5 Calibrate

**Module:** `calibrate.py`  
**Entry:** `run_calibration(config) -> list[dict]`

Reads `graded_results.json` and for each question:
1. Collects all student scores.
2. Computes mean and sample standard deviation (n-1 denominator).
3. Flags entries with `|z| > 2` as outliers.

Writes `calibration_report.json` and returns the list of flagged entries. A minimum
of 2 scores per question is required before statistics are computed.

---

### 4.6 Export

**Module:** `export.py`  
**Key functions:** `export_all(config)`, `export_autograder_zip(config)`

`export_all`:
- Reads `graded_results.json`.
- Writes `output/gradescope/{StudentName}.json` in Gradescope autograder format
  (array of `{name, score, max_score, output, visibility}` test entries).
- Applies `cfg.gradescope_title_mapping` to rename QID labels in Gradescope output.
- If `grade_only` is set, only those QIDs appear in the Gradescope output.
- Writes `Final_Grades.xlsx` with one row per student and one column per question.

`export_autograder_zip`:
- Bundles all per-student JSONs from `gradescope/` into a Gradescope autograder ZIP.
- The embedded `run_autograder` script resolves the submission owner from
  `submission_metadata.json` and copies a matching pre-computed result to
  `/autograder/results/results.json`.
  - It performs exact normalized-name matching only.
  - If multiple exact-normalized matches are found, it emits an explicit ambiguous-match error
    instead of using fuzzy fallback matching.
- ZIP entries for `setup.sh` and `run_autograder` use `create_system=3` (Unix) and
  `external_attr = 0o755 << 16` to set executable bits for Gradescope.

---

## 5. Prompt system

Prompts live exclusively on the filesystem. There are no prompt strings embedded in
Python code. The loading path:

```
prompts/{assignment_name}/{name}.md   ← checked first
prompts/DEFAULT/{name}.md             ← fallback
FileNotFoundError                     ← strict failure if neither exists
```

Available prompt names used in the pipeline:

| Name | Used by | Purpose |
|---|---|---|
| `grade_system` | `grade_group` | System prompt for the grading LLM |
| `rubric_system` | `rubric._get_rubric_generation_prompt` | System prompt for rubric generation |
| `review_system` | `rubric._get_rubric_review_prompt` | System prompt for rubric review pass |

To customize prompts for a specific assignment, create files in
`prompts/{assignment_name}/`. Files not present there fall back to `prompts/DEFAULT/`.

### Grading prompt structure (`build_group_prompt`)

For each group:
1. One system message with the `grade_system` prompt text.
2. One user message containing, in order:
   - For each question in the group:
     - Question header with points
     - Solution content (question markdown, code, output, markdown answer, images)
       — if `include_reference_in_grading` is False, only the question text and rubric are included
     - Rubric (if present in `cfg.rubrics`)
   - Token budget handling: per-question output text is truncated using a character
     budget derived from estimated token usage.
   - Student submission section wrapped in `<<<STUDENT_SUBMISSION>>>` / `<<<END_STUDENT_SUBMISSION>>>` delimiters.

Image payloads are added as vision message parts with
`{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`,
incurring `TOKENS_PER_IMAGE = 1000` in the token budget estimate.

### Prompt injection protection

All student-supplied text is run through `_sanitize_student_text` before insertion,
which replaces `<<<` / `>>>` delimiter sequences with visually similar but structurally
inert characters (`«` / `»`). This prevents a malicious student from escaping the
`<<<STUDENT_SUBMISSION>>>` boundary in the prompt.

---

## 6. Grading engine internals

### LLM response validation chain

```
client.chat.completions.create(response_format={"type": "json_object"})
  → raw_content (str)
  → parse_llm_json(raw_content)      # tolerates markdown fences
  → GradingResponse.from_raw(raw, group)
      → normalize key: "Q1.1" / "q1.1" → "1.1"
      → QuestionGrade.model_validate(v) per entry
          → coerce_score: float(v)
          → coerce_confidence: "high"|"medium"|"low"
      → fill missing QIDs: score=0, feedback="[not returned by LLM]"
  → check for placeholder feedbacks → retry if any found
  → return GradingResponse
```

### Feedback sentinel constants

`grading_models.py` defines:
- `SKIP_FEEDBACKS = ("[skipped - not in grade_only]", "[not included in grading groups]")`
- `NO_SUBMISSION = "[no submission]"`

These are used throughout the codebase to identify questions that should not
contribute to totals. `compute_totals_from_questions` (`grade.py`) excludes any
question whose feedback (stripped) appears in `SKIP_FEEDBACKS`.

`_normalize_no_submission_feedback` detects common LLM phrasings for "not submitted"
and normalizes them to the canonical `NO_SUBMISSION` sentinel.

### Temperature policy

`temperature_for_model(model)` returns:
- `1.0` for `gpt-5*` family (reasoning models that only support temperature=1)
- `0.0` for all other models (deterministic output for reproducibility)

---

## 7. Web app and API layer

`app.py` is a FastAPI application serving both the REST API and the static UI.

### Concurrency controls

Three threading locks:
```python
_grading_lock   # prevents two concurrent grading runs (Write: graded_results.json)
_results_lock   # protects results reads/writes (grading loop + manual review saves)
_rubric_lock    # protects rubric config writes during generation
```

Grading and rubric generation run in background threads, streaming progress via
Server-Sent Events (SSE) through `_threaded_sse_response`. The async route suspends
on a queue that the background thread feeds via `loop.call_soon_threadsafe`.

### Config update behavior (`PUT /config`)

The endpoint accepts a partial config payload from the UI. The incoming dict is
deep-merged with defaults and existing config before saving:

1. Load current full config via `load_config()`.
2. If assignment name changes and `solution_notebook` is omitted, force
  `solution_notebook = ""` to avoid stale cross-assignment paths.
3. Deep-merge `_default_config()`, existing config, and incoming payload.
4. Validate via `AppConfig.model_validate(merged)`.
5. Save via `save_config(merged)`.

The UI sends only the fields the user changed; the merge preserves everything else.

### Assignment switching

When a `PUT /config` payload contains a different `assignment_name`, the app:
1. Detects the change (`incoming_assignment != current_assignment`).
2. Saves the updated payload to the active assignment config path.
3. Re-initializes file logging to the new assignment's `autograder.log`.

Creation/loading of `output/{assignment_name}/config.yaml` is handled by
`POST /load-or-create`, which sets the in-memory active config path.

### SSE streaming routes

`GET /grade`, `GET /generate-rubrics`, `POST /grade/{student_name}` all return
`EventSourceResponse` objects. The UI subscribes and renders progress events as
they arrive without polling.

---

## 8. Security guardrails

| Threat | Mitigation |
|---|---|
| Prompt injection via student notebook | `_sanitize_student_text` replaces `<<<` / `>>>` with `«` / `»` before any student content enters a prompt |
| Path traversal in API routes | `_safe_path(base, user_input)` resolves and checks that the result is under `base`; raises HTTP 400 otherwise |
| Untrusted YAML (student names) | Student names come only from Gradescope metadata, not from notebook content |
| Concurrent writes to `graded_results.json` | `_results_lock` wraps all reads and writes in `batch_grader` and `app.py` |
| API key exposure | Loaded via `dotenv` (`.env` file with `key=...`) or `OPENAI_API_KEY`; never logged or returned in API responses |
| Malformed LLM JSON | `parse_llm_json` extracts the first balanced `{...}` block; `GradingResponse.from_raw` replaces unparseables with safe defaults |

---

## 9. Testing strategy

Tests live in `tests/` and are run with `pytest tests/ -q`.

| File | What it covers |
|---|---|
| `test_utils.py` | Config load/save/defaults, `AppConfig` serialization helpers, `ensure_app_config`, path resolution |
| `test_parse.py` | Notebook parsing with mock cell structures |
| `test_grade.py` | `grade_student` and `grade_group` logic including `grade_only`, merge, skip, retry |
| `test_rubric.py` | Rubric generation and review pass logic |
| `test_export.py` | Excel and Gradescope JSON output, `gradescope_title_mapping` |
| `test_gather.py` | Submission extraction from metadata |
| `test_calibrate.py` | Z-score computation and outlier flagging |
| `test_app.py` | FastAPI endpoints including config CRUD, grading SSE, export downloads |
| `test_prompt_builder.py` | Prompt construction, sanitization, token budgeting |
| `test_linter_export.py` | Linter ZIP creation |
| `test_integration.py` | End-to-end parse → grade → export with mocked LLM |

LLM calls are always mocked in tests via `unittest.mock.patch`. Tests never hit
the OpenAI API.

### Key test patterns

Configs in tests are plain dicts and must contain all required fields explicitly
(no reliance on `load_config` defaults). `AppConfig` has defaults for all fields,
so minimal config dicts work:

```python
config = {
    "grading": {"question_groups": [["1.1"]], "grade_only": None},
    "model": DEFAULT_MODEL,
    "max_prompt_tokens": 80000,
    "rubrics": {},
}
```

---

## 10. Design principles and conventions

### Object-first config

`AppConfig` is parsed once at the entry boundary. Typed objects flow through the
pipeline. Raw dicts only appear at IO edges (YAML read/write, API request/response).

```
load_config() → dict          # IO boundary: YAML → dict
ensure_app_config(dict) → AppConfig   # coerce once
grade_student(cfg: AppConfig | dict)  # accepts both for flexibility
```

### Question ID canonicalization

Canonical QIDs are numeric strings: `"1.1"`, `"2.3"`, etc.

Anywhere a QID could arrive with a `Q` prefix (from LLM output, UI, YAML),
`GradingResponse.from_raw` normalizes it: `k.strip().lstrip("Qq").strip()`.

Config helpers (`get_active_grade_only`, `get_effective_question_groups`) operate on
already-canonical IDs. Do not compare raw LLM keys to config QIDs without normalizing.

### Incremental saves

`graded_results.json` is written after every student completes grading, not at the
end. This means a crashed or interrupted run is resumable with no lost work. The
batch grader reads existing results at startup and skips students already present
(unless retryable).

### Assignment-scoped logging

Every pipeline module obtains its logger via:

```python
logger = get_job_logger(config, __name__)
```

This creates a logger named `autograder.{assignment_name}.{module}` backed by a
`FileHandler` writing to `output/{assignment_name}/autograder.log`. Switching
assignments switches the log file automatically.

### `extra="ignore"` on AppConfig

Unknown YAML keys are silently dropped when loading into `AppConfig`. This prevents
old config files with deprecated keys from failing validation, and ensures that
injecting extra fields via the API cannot affect behavior.

---

## 11. Common extension scenarios

### Add a new prompt

Create `prompts/DEFAULT/{name}.md` (or `prompts/{assignment_name}/{name}.md` for
an assignment-specific override). Call `load_prompt("{name}", assignment_name=...)`.

### Add a new config field

1. Add it to the appropriate Pydantic model (`AppConfig`, `GradingConfig`, etc.)
   with a default value.
2. Add it to `_apply_config_defaults` if it needs to be back-filled in old configs
   for load-time safety.
3. The assignment config is fully authoritative; no special root-override handling
   is needed for new fields.
4. Update `_default_config` in `app.py` so the UI sends it on fresh setup.

### Add a new pipeline stage

1. Create a module with a `main()` entry point and a primary function with
   signature `def run_{stage}(config: AppConfig | dict) -> ...`.
2. Add the stage name to `main.py`'s step list and dispatch block.
3. Add a route to `app.py` if it needs a UI trigger.
4. Write tests in `tests/test_{stage}.py`.

### Regrade a subset of questions

Use `grade_only` + `grade_only_merge`:

```yaml
grading:
  grade_only: ["2.1", "2.2"]
  grade_only_merge: true
```

Then run `python main.py --steps grade --no-write-config`. Only questions `2.1` and
`2.2` will be re-graded; all others retain their existing scores. Totals are
recomputed from the merged result.

### Add a new OpenAI model

Add the model name and pricing (input, output per 1M tokens) to `MODEL_PRICING` in
`grading_models.py`. Update `temperature_for_model` in `utils.py` if the model has
a temperature restriction.
