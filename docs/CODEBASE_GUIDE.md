# Codebase Guide

A deeper technical reference than the README. Start here if you want to understand
how the pieces fit together, trace data through the pipeline, or make non-trivial changes.

**Documentation order:** see `[docs/README.md](./README.md)`. For **product / accuracy / roadmap** (not module-by-module reference), see `[AUTOGRADER_DESIGN_REVIEW.md](./AUTOGRADER_DESIGN_REVIEW.md)` Sections A–F. **Refactoring practices, tests, and invariants** are in this guide (§9–11), not duplicated elsewhere.

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
  - [optional-genai-detection](#47-optional-genai-detection-pass)
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
├── config_models.py        AppConfig, ParsingConfig, GradingConfig, config I/O, paths
├── grade.py                Per-student and per-group grading logic
├── grading_helpers.py      grade_only filtering, needs_merge, skipped_feedback
├── grading_models.py       Pydantic schemas for all LLM structured outputs
├── linter_export.py        Pre-deadline format linter autograder
├── llm/
│   ├── json_runner.py      Structured Responses API, retries, run_jobs, extract_llm_questions
├── llm_client.py           OpenAI client creation and temperature helpers
├── main.py                 CLI entry point
├── parse_notebook.py       Notebook → structured JSON parser
├── genai_detection.py      Optional GenAI suspicion pass (merges flags into graded_results)
├── pipeline_runner.py      Thin wrappers for app endpoints (run_gather, run_parse, etc.)
├── prompt_builder.py       Prompt construction, sanitization, JSON extraction
├── results_models.py       GradedResult, ParsedNotebook, Question (on-disk schemas)
├── results_store.py        load_results → list[GradedResult]; save/update coerce dict rows
├── rubric_generate.py      Rubric LLM generation + optional review orchestration
├── rubric_review.py        Rubric review pass (second LLM pass)
├── token_usage.py          TokenUsage, MODEL_PRICING, cost calculation, usage helpers
├── utils.py                Assignment-scoped logging, filename sanitization
├── zip_helpers.py          Shared ZIP archive helper (write_to_zip)
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


| Module               | Responsibility                                                       | Calls into                                                         |
| -------------------- | -------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `main.py`            | CLI, config write, pipeline orchestration                            | All pipeline modules                                               |
| `app.py`             | FastAPI app factory; includes routers from `api/routers/`          | `api.state`, router modules                                        |
| `config_models.py`   | AppConfig, ParsingConfig, GradingConfig, default_config              | nothing (leaf)                                                     |
| `grading_helpers.py` | grade_only filtering, needs_merge, skipped_feedback                  | grading_models                                                     |
| `pipeline_runner.py` | Thin wrappers for gather, parse, calibrate, export, estimate, genai | gather, parse_notebook, calibrate, export, estimate, `genai_detection` |
| `results_store.py`   | load_results, save_results, update_student, load_results_with_backup | nothing (leaf)                                                     |
| `utils.py`           | Assignment-scoped logging, filename sanitization                     | config_models                                                      |
| `grading_models.py`  | LLM response Pydantic schemas, constants                             | nothing (leaf)                                                     |
| `prompt_builder.py`  | Prompt loading, token counting, sanitization, JSON parsing           | `utils`                                                            |
| `parse_notebook.py`  | Notebook → parsed JSON                                               | `utils`                                                            |
| `grade.py`           | `grade_group`, `grade_student`, post-processing                      | `prompt_builder`, `grading_models`, `utils`                        |
| `batch_grader.py`    | Sequential/parallel grading, resume logic                            | `grade`, `results_store`, `utils`                                  |
| `rubric_generate`, `rubric_review` | Rubric generation + optional review pass                             | `prompt_builder`, `utils`, `llm`                                   |
| `gather.py`          | Submission extraction                                                | `utils`                                                            |
| `export.py`          | Excel + Gradescope export                                            | `utils`                                                            |
| `calibrate.py`       | Outlier detection                                                    | `utils`                                                            |
| `estimate.py`        | Cost projection                                                      | `prompt_builder`, `utils`                                          |
| `linter_export.py`   | Format linter zip                                                    | `parse_notebook`, `utils`                                          |
| `genai_detection.py` | Post-grade suspicion flags on questions                             | `llm.json_runner`, `prompt_builder`, `results_store`               |


### Stable entrypoints

**Documentation index:** `[docs/README.md](./README.md)`. Recorded product choices live in `[DECISIONS.md](./DECISIONS.md)`.

Prefer these surfaces when adding features so CLI and web app stay aligned:

- **CLI:** `[main.py](../main.py)` — uses `[pipeline_runner.py](../pipeline_runner.py)` for gather, parse, calibrate, and export (same calls as HTTP routes).
- **HTTP:** `[app.py](../app.py)` and `[api/routers/](../api/routers/)` — load config via `[api/state.py](../api/state.py)` (`get_active_app_config()` for pipeline work); call `pipeline_runner`, `batch_grader`, `grade`, `export`, etc.
- **Shared step wrappers:** `[pipeline_runner.py](../pipeline_runner.py)` (`run_gather`, `run_parse`, `run_export`, …).
- **Config I/O:** `load_app_config` / `save_config` in `config_models.py` for assignment YAML; `ensure_app_config` at dict/`AppConfig` boundaries.

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

### Loading logic (`load_app_config`)

`load_app_config(config_path: Path)` in `config_models.py` requires an explicit path to the assignment config.

```
config_path must point to output/{assignment_name}/config.yaml
  → load YAML as dict
  → infer project_root as config_path.parent.parent.parent
  → resolve relative paths against project_root
  → normalize derived paths (output_dir, submissions_dir, parsed_dir)
  → validate via ensure_app_config → return AppConfig
```

The web app caches the loaded `AppConfig` via `get_active_app_config()` in `api.state`
(call `invalidate_config_cache()` after saving). Config is saved with `save_config()`
from `config_models.py`.

### `AppConfig` schema (`config_models.py`)

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
    section_regex: str      # see section 4.2 regex contract
    question_regex: str     # see section 4.2 regex contract
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
    },
    "_usage": { "prompt_tokens": 1200, "completion_tokens": 400 }
  }
]
```

After an optional GenAI pass, question objects may also include `suspicious_genai` (bool) and `suspicious_genai_note` (string); see `results_models.Question` (`extra="allow"`).

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

Writes next to the assignment output root (parent of `submissions/`):

- `email_stem_map.json` — student email → notebook stem (used by Gradescope autograder for lookup).

Handles:

- ZIP input (extracts first, then processes)
- `assignment_*_export` subdirectory layout from Gradescope
- Missing and duplicate submissions (returned in result dicts with appropriate status)

Does **not** modify `config.yaml`.

---

### 4.2 Parse

**Module:** `parse_notebook.py`  
**Key functions:** `parse_notebook(nb_path, config)`, `parse_all_students(config)`

**Full contract:** the `parse_notebook.py` module docstring (“Notebook parsing contract”) lists every assumption; this section summarizes it for the guide.

**Cell types and order**

- Only **markdown** cells can open a section or question. **Code** cells are never regex
  targets for structure. Other cell types are ignored for matching but still occupy indices
  in the linear `cells` list.
- Cells are scanned in order. If a markdown cell matches **both** `section_regex` and
  `question_regex`, **section wins** (that cell is treated as a section header only).

**`section_regex`**

- Compiled with **`re.IGNORECASE`**.
- **`re.search`** on the **entire** joined `source` text of the cell (first match).
- **Capture group 1** must be the section id string (e.g. `"1"`) used as `sections` keys
  and with question captures to form QIDs `sec.qnum`.
- Re-encountering the same section id overwrites `overview_markdown` for that section.

**`question_regex`**

- Compiled with **`re.MULTILINE`** (so `^` can match the start of any line inside a cell).
- **`re.search`** on the entire cell text (first match).
- Must expose either:
  - **Four capturing groups:** optional prefix (e.g. dash), section id, question number,
    points — parser uses **groups 2, 3, 4**; or
  - **Three groups:** section id, question number, points — parser uses **groups 1, 2, 3**.
- Points are parsed with `int()`.

**Answers**

1. A markdown cell matching `section_regex` opens a new section.
2. A markdown cell matching `question_regex` opens a new question (within the section
   implied by the question match, creating the section bucket if needed).
3. Following **code** cells contribute code, stdout/plain text, and optional images
   (when `parsing.keep_images` is true).
4. Following **markdown** cells append non-empty stripped text to the answer until the
   next markdown cell that matches `section_regex` or `question_regex`.

If `section_regex` is too loose (e.g. it matches student `### 1. …` subheadings), answers
stop early. Prefer a pattern that matches real handout section lines only (distinctive
HTML, wording, or heading level).

**`extract_qids_from_notebook`**

Same `question_regex` semantics: markdown only, one `search` per cell, same 3- vs 4-group
rule; used to list QIDs and detect duplicates across cells.

`parse_all_students` parses the solution notebook (writes `solution_parsed.json`),
then parses every `.ipynb` in `submissions_dir` (writes one `.json` per student to
`parsed_dir`).

**Important:** Images are preserved as base64 payloads in the parsed output when
`parsing.keep_images = true`. These are included in grading prompts as vision
message parts, which increases token usage but enables grading of plots.

---

### 4.3 Generate rubrics

**Modules:** `rubric_generate.py` (`generate_rubrics`, …), `rubric_review.py` (`review_rubrics`, …)  
**Key function:** `generate_rubrics(config, client, progress_callback, group_indices)`

Flow:

1. Load `solution_parsed.json` from `output_dir`.
2. For each question group (or the subset specified by `group_indices`), call the LLM
  with the solution content and `rubric_system` prompt.
3. Parse the returned JSON into a `{qid: {points, items: [{description, deduction}]}}` dict.
  The object must include **every** question in the group; each entry must include `points` (no silent backfill from the solution).
  Omissions fail validation and trigger JSON LLM retries, then a `"[generation failed]"` placeholder rubric for that group if retries are exhausted.
4. If `rubric_review` is enabled, run a second LLM pass (`review_system` prompt) per group
  that softens rubric criteria that hardcode reference-solution-specific values
   not required by the question text.
5. If `group_indices` is set (partial regeneration), merge new rubrics into the
  existing rubric dict rather than replacing everything.

**Rubric review** is a safety pass. It does *not* change point values or deduction
amounts — only description text. The review pass reverts any group where deductions
no longer sum to points after the LLM's rewrite.

**Parallelism:** `cfg.workers` controls parallel rubric **generation** and **review** when `workers > 1` and there is more than one group. Parallel bulk grading uses the same entrypoint: `llm.json_runner.run_jobs` (handles both sequential and parallel dispatch internally).

---

### 4.4 Grade

**Modules:** `grade.py`, `batch_grader.py`

#### Per-group (`grade_group`)

One LLM call per question group. Flow:

1. Build prompt via `build_group_prompt` (see [Prompt system](#5-prompt-system)).
2. Run `llm.json_runner.execute_llm_task` (messages, model, caps, `response_model=grading_models.GradingLlmResponse`). Inside the runner: `llm.json_runner.complete_structured` (wraps `client.responses.parse` with `text_format=<Pydantic model>` — OpenAI Structured Outputs, requires gpt-4o / gpt-4.1-* or later), global retry budget `llm.json_runner.MAX_JSON_LLM_ATTEMPTS` (= `MAX_VALIDATION_RETRIES + 1`), exponential backoff via `llm.json_runner.retry_with_exponential_backoff`.
3. The API guarantees the response matches `grading_models.GradingLlmResponse` (`grades: list[QuestionGrade]`) — no JSON parsing or fence stripping needed.
4. Post-process in `grade.py` (`_postprocess_grade_group`): normalizes each row’s `question_id` (`Q1.1` / `q1.1` → `1.1`), deduplicates by QID, checks all group questions are present (raises `ValueError` → retry if any missing), returns `list[QuestionGrade]` (no dict copy of the group).
5. On exhaustion, `fallback_factory` returns a list of `QuestionGrade` rows (one per QID) with zero scores and `[grading failed after retries]` feedback.

`effective_max_completion` is capped at `max(2048, len(group) * 1024)` to avoid
burning completion budget on single-question groups.

#### Per-student (`grade_student`)

Iterates over all groups from `grading_config.get_effective_groups()`:

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

**Resume behavior:** Reads existing `graded_results.json` at startup via `results_store.load_results_with_backup` (backs up corrupted file to `*.broken`). Students already in the file (and not in a retryable state) are skipped. The file is incrementally updated after each student via `results_store.save_results` (deduplicates by student_name).

**Parallel mode:** When `workers > 1`, uses `llm.json_runner.run_jobs` over the student work queue (thread pool). The implementation shares the same OpenAI client object passed into `grade_all_students` across worker threads.

#### `grade_only_merge` flow

When `grade_only` + `grade_only_merge` are both set:

1. `needs_merge(existing, grade_only)` (in `grading_helpers`) checks whether the specified
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
(array of `{name, number, score, max_score, status, output, output_format, visibility}` test entries; visibility defaults to `after_published`).
- Applies `cfg.gradescope_title_mapping` to rename QID labels in Gradescope output.
- If `grade_only` is set, only those QIDs appear in the Gradescope output.
- Writes `Final_Grades.xlsx` with one row per student and one column per question.

`export_autograder_zip`:

- Bundles all per-student JSONs from `gradescope/` into a Gradescope autograder ZIP under
`results/{stem}.json`.
- Includes **`email_stem_map.json`** mapping student emails to notebook stems for runtime lookup.
- Ships **`gradescope_runtime.py`** at the ZIP root; the thin `run_autograder` adds
`/autograder/source` to `sys.path` and calls `gradescope_runtime.main()`.
- Runtime lookup: reads `users[0].email` from `submission_metadata.json`, looks up stem in
`email_stem_map.json`, copies `results/{stem}.json` to output.
- ZIP entries for `setup.sh` and `run_autograder` use Unix executable bits via `zip_helpers.write_to_zip`.

**Module:** `gradescope_runtime.py` (Gradescope-side email-based resolver).

---

### 4.7 Optional GenAI detection pass

**Module:** `genai_detection.py`  
**Pipeline wrapper:** `pipeline_runner.run_genai_detection(config)`

Runs **after** grading. Loads `graded_results.json` and per-student `parsed/*.json`, calls the detector model for eligible questions, and writes `suspicious_genai` / `suspicious_genai_note` on each question dict. **Does not change scores.** See also [extension scenario](#genai-suspicion-pass-optional-second-llm-call) below.

**Invoke:** `python main.py --steps detect-genai`, `POST /detect-genai`, or the Grade tab action (coordinates with `grading_lock` / `results_lock` per API routes).

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


| Name            | Used by                                | Purpose                              |
| --------------- | -------------------------------------- | ------------------------------------ |
| `grade_system`  | `grade_group`                          | System prompt for the grading LLM    |
| `rubric_system` | `rubric_generate.get_rubric_generation_prompt` | System prompt for rubric generation  |
| `review_system` | `rubric_review.get_rubric_review_prompt`         | System prompt for rubric review pass |
| `genai_detection_system` | `genai_detection.run_genai_detection`     | System prompt for optional GenAI suspicion pass |


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
    - Student submission section wrapped in `<<<STUDENT_SUBMISSION>>>` / `<<<END_STUDENT_SUBMISSION>>>` delimiters.

Long text fields (reference and student code, output, markdown) share one character cap
derived from `max_prompt_tokens` (`prompt_builder._grading_body_char_cap` — roughly 4×
tokens, clamped). No per-question token estimation in the builder.

Image payloads are added as vision message parts with
`{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`.
`estimate_tokens` still uses `TOKENS_PER_IMAGE = 1000` when projecting costs from built messages.

### Prompt injection protection

All student-supplied text is run through `_sanitize_student_text` before insertion,
which replaces `<<<` / `>>>` delimiter sequences with visually similar but structurally
inert characters (`«` / `»`). This prevents a malicious student from escaping the
`<<<STUDENT_SUBMISSION>>>` boundary in the prompt.

---

## 6. Grading engine internals

### LLM response validation chain

```
execute_llm_task(..., response_model=GradingLlmResponse, postprocess=_postprocess_grade_group, fallback_factory=…)
  → complete_structured(...)        # client.responses.parse(text_format=GradingLlmResponse)
                                    # API guarantees schema — no JSON parsing needed
  → _postprocess_grade_group(GradingLlmResponse)
      → normalize each row’s question_id; dedupe by canonical QID
      → check all group QIDs present → raise ValueError → retry if any missing
  → return list[QuestionGrade]  →  _store_group_grades builds per-student question dicts
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

### Token usage

All runtime aggregation uses `token_usage.TokenUsage`: combine with `.merged(other)`,
test non-zero with `.has_tokens()`, sum with `.total_tokens`. Serialized JSON uses
the same keys as the dataclass fields (`prompt_tokens`, `completion_tokens`, the
usual OpenAI usage shape). Convert at boundaries only: `.to_json_dict()` when
emitting (graded_results `_usage`, SSE `usage`, estimate API payloads) and
`.from_json_dict()` when ingesting those payloads.

### Structured-output row types

Row types are shared for LLM wire and pipeline: **`QuestionGrade`** (grading), **`GenaiQuestionResult`** (GenAI pass), **`RubricItem`** nested in **`RubricQuestionLlm`** (rubric generation). Numeric constraints that can upset strict JSON Schema use **`@field_validator`** (e.g. nonnegative `deduction` on `RubricItem`) instead of `Field(ge=…)` on wire-nested models. **`execute_llm_task`** **`postprocess`** enforces group completeness and canonical QIDs (`normalize_qid`). Review-only rows use **`RubricReviewItem`** / **`RubricReviewQuestion`** inside **`RubricReviewResponse`** (descriptions only; not a second `RubricItem`).

### Temperature policy

`temperature_for_model(model)` returns:

- `1.0` for `gpt-5*` family (reasoning models that only support temperature=1)
- `0.0` for all other models (deterministic output for reproducibility)

---

## 7. Web app and API layer

`app.py` is a FastAPI application serving both the REST API and the static UI.

The Review tab fetches per-student parsed JSON (`GET /parsed/{student_name}`) and renders `answer_text_concat` plus any `image/png` or `image/jpeg` entries under `answer_cells[].images` as inline `<img>` data URLs (`ui/js/app.js`).

### Export (`POST /export`, file downloads)

`POST /export` runs `export_all`: writes per-student Gradescope JSONs under `gradescope/`, `Final_Grades.xlsx`, and regenerates `gradescope_autograder.zip` when there is at least one graded student. The JSON body includes `students`, `gradescope_dir`, `gradescope_files`, `excel_path`, and `autograder_zip` (empty strings and zero counts when `graded_results.json` is empty). The Export tab shows those paths and uses `estimateErrorMessage` for HTTP errors. `GET /export/excel`, `GET /export/autograder-zip`, and `GET /export/linter-zip` serve downloads; the autograder route calls `run_export` then `export_autograder_zip` so a direct download stays aligned with disk state. Download `href`s are prefixed with the same `API` base as `fetch` (`ui/js/shared.js`).

### Concurrency controls

Three threading locks on `api.state`:

```python
grading_lock   # prevents two concurrent grading runs (writes graded_results.json)
results_lock   # protects results reads/writes (grading loop + manual review saves)
rubric_lock    # rubric generation / SSE exclusivity
```

`GET /grade` and `GET /generate-rubrics` stream progress via Server-Sent Events (SSE)
through `api.sse.threaded_sse_response`. The async route waits on a queue fed by
`loop.call_soon_threadsafe` from a background thread.

**Blocking work in routes:** CPU- or disk-heavy steps (e.g. `run_parse`) run inside
`asyncio.to_thread(...)` so the event loop stays responsive; lightweight handlers may stay synchronous.

### Config update behavior (`PUT /config`)

The endpoint accepts a partial config payload from the UI. The incoming dict is
deep-merged with defaults and existing config before saving:

1. Load current full config via `load_config()`.
2. If assignment name changes and `solution_notebook` is omitted, force
  `solution_notebook = ""` to avoid stale cross-assignment paths.
3. Deep-merge `default_config("default")`, existing config, and incoming payload.
4. Validate via `ensure_app_config(merged)` (same coercion as the rest of the pipeline).
5. Save via `save_config(validated.model_dump(mode="python"), config_path)` and call `state.invalidate_config_cache()`.

The UI sends only the fields the user changed; the merge preserves everything else.

### Assignment switching

When a `PUT /config` payload contains a different `assignment_name`, the app:

1. Detects the change (`incoming_assignment != current_assignment`).
2. Saves the updated payload to the active assignment config path.
3. Re-initializes file logging to the new assignment's `autograder.log`.

Creation/loading of `output/{assignment_name}/config.yaml` is handled by
`POST /load-or-create`, which sets the in-memory active config path.

### SSE streaming routes

`GET /grade` and `GET /generate-rubrics` return `EventSourceResponse` (SSE). Other
long routes (e.g. `POST /parse`, `POST /grade/{student_name}`) use `asyncio.to_thread`
for blocking work and return ordinary JSON responses.

---

## 8. Security guardrails


| Threat                                     | Mitigation                                                                                                                       |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| Prompt injection via student notebook      | `_sanitize_student_text` replaces `<<<` / `>>>` with `«` / `»` before any student content enters a prompt                        |
| Path traversal in API routes               | `safe_path` in `api/helpers.py` resolves and checks the path stays under `base`; raises HTTP 400 otherwise                       |
| Blank `solution_notebook` on `POST /parse-solution` | `resolve_solution_notebook_path` returns `None` so the repo root is never opened as a notebook (avoids `PROJECT_ROOT / ""`); HTTP 404 with a clear message |
| Untrusted YAML (student names)             | Student names come only from Gradescope metadata, not from notebook content                                                      |
| Concurrent writes to `graded_results.json` | `results_lock` wraps reads and writes; `results_store` used by both `batch_grader` and API routes                                |
| API key exposure                           | Loaded via `dotenv` — use `**OPENAI_API_KEY`** in `.env` (legacy `key` is deprecated); never logged or returned in API responses |
| Malformed LLM output                       | `complete_structured` uses OpenAI Structured Outputs — schema is enforced by the API; content errors (missing QIDs) raise `ValueError` and retry via `execute_llm_task`   |


---

## 9. Testing strategy

Tests live in `tests/` and are run with `pytest tests/ -q`.


| File                     | What it covers                                                                                     |
| ------------------------ | -------------------------------------------------------------------------------------------------- |
| `test_utils.py`          | Config load/save/defaults, `AppConfig` serialization helpers, `ensure_app_config`, path resolution |
| `test_parse.py`          | Notebook parsing with mock cell structures                                                         |
| `test_grade.py`          | `grade_student` and `grade_group` logic including `grade_only`, merge, skip, retry                 |
| `test_rubric.py`         | Rubric generation and review pass logic                                                            |
| `test_export.py`         | Excel and Gradescope JSON output, `gradescope_title_mapping`, autograder ZIP layout                  |
| `test_gather.py`         | Submission extraction from metadata, `email_stem_map.json`                                           |
| `test_calibrate.py`      | Z-score computation and outlier flagging                                                           |
| `test_app.py`            | FastAPI endpoints including config CRUD, `POST /parse-solution`, grading SSE, export downloads      |
| `test_prompt_builder.py` | Prompt construction, sanitization, token budgeting                                                 |
| `test_linter_export.py`  | Linter ZIP creation                                                                                |
| `test_results_store.py`  | `load_results`, `save_results`, `load_results_with_backup`, update_student                         |
| `test_integration.py`    | End-to-end parse → grade → export with mocked LLM                                                  |
| `test_queue_estimate.py` | `load_grade_queue` vs `estimate_grade` alignment                                                   |
| `test_usage_helpers.py`  | `token_usage` helpers: `detach_usage_from_graded_result`, `merge_graded_usage`, SSE summary event |
| `test_results_models.py` | `GradedResult` / disk round-trip                                                                   |
| `test_genai_detection.py` | Optional GenAI suspicion pass; `llm.json_runner.complete_structured` mocked; scores unchanged                      |
| `test_json_runner.py`     | `execute_llm_task` retries, exhaustion, fallbacks; `run_jobs` smoke test; `extract_llm_questions` validation |
| `test_batch_grader.py`    | `load_grade_queue` resume logic, skip-already-graded behavior                                      |
| `test_config_models.py`   | `normalize_qid`, config load/save round-trip, `GradingConfig` methods, `load_solution_parsed`      |
| `test_question_tags.py`   | Tag extraction in parser, `tag_notebook` script injection, prompt type instruction injection        |


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
load_app_config(path) → AppConfig   # preferred for CLIs / pipeline entry
load_config(path) → dict          # IO boundary: YAML → dict (API, tests, merges)
ensure_app_config(dict) → AppConfig   # coerce once
grade_student(cfg: AppConfig | dict)  # accepts both for flexibility
```

### Question ID canonicalization

Canonical QIDs are numeric strings: `"1.1"`, `"2.3"`, etc.

Anywhere a QID could arrive with a `Q` prefix (from LLM output, UI, YAML),
use **`normalize_qid()`** from `config_models` — e.g. grading postprocess copies each `QuestionGrade` with a normalized `question_id`.

Config helpers (`filter_groups_by_grade_only`, `needs_merge` in `grading_helpers.py`; `get_grade_only()` and `get_effective_groups()` as methods on `GradingConfig`) operate on
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
2. Add a default on `AppConfig` / nested models (and rely on `merge_partial_config_dict`) if it needs to be back-filled in old configs
  for load-time safety.
3. The assignment config is fully authoritative; no special root-override handling
  is needed for new fields.
4. Update `default_config()` in `config_models.py` so the UI sends it on fresh setup (app uses `default_config("default")` when no assignment is loaded).

### Add a new pipeline stage

1. Implement the stage (primary callable taking `AppConfig | dict`, same as existing modules).
2. Add the stage name to `main.py`'s `--steps` choices and the `if "…" in steps:` dispatch block.
3. If it needs a UI trigger, add HTTP routes in `api/routers/` (see `pipeline_routes.py`,
  `grade_routes.py`, …) and, if you add a new router module, register it in `app.py` with
  `include_router`.
4. **Shared entrypoints:** `gather`, `parse`, `calibrate`, and `export` use thin wrappers in
  `pipeline_runner.py` so CLI (`main.py`) and HTTP call the same code. **`grade` and
  `generate-rubrics` are not in `pipeline_runner`** — they are imported directly (`batch_grader`,
  `rubric_generate.generate_rubrics`). Follow whichever pattern fits the new step.
5. Write tests in `tests/test_{stage}.py` (or extend an existing test file).

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
`token_usage.py`. Update `temperature_for_model` in `llm_client.py` if the model has
a temperature restriction.

### GenAI suspicion pass (optional, second LLM call)

- **Purpose:** Triage only — flag answers that may look LLM-assisted; **never** changes scores (see `DECISIONS.md`).
- **Code:** `genai_detection.py` — `llm.json_runner.execute_llm_task` with `response_model=GenaiLlmResponse` (`results: list[GenaiQuestionResult]`), post-process `_postprocess_genai_detection` → `list[GenaiQuestionResult]` (normalized `question_id`, completeness check), and `fallback_factory` on exhaustion (logs, returns empty list, skips student). Requires every `expected_qid` in the batch (attempt budget `llm.json_runner.MAX_JSON_LLM_ATTEMPTS`, structured logging via `get_job_logger`). User message built by `genai_detection.build_genai_detection_user_message`. Reads `graded_results.json` + `parsed/*.json`, system prompt `prompts/DEFAULT/genai_detection_system.md`. Merges `suspicious_genai` / `suspicious_genai_note`; re-runs overwrite prior flags.
- **Invoke:** `python main.py --steps detect-genai`, `POST /detect-genai`, or **Run GenAI detection** on the Grade tab (blocked while bulk grading holds `grading_lock`; writes use `results_lock`).