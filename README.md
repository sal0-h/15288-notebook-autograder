# Gradescope Notebook Autograder

LLM-assisted grading pipeline for Jupyter notebook assignments submitted through Gradescope.

This repository is built for course staff who need a practical workflow to:
- ingest a Gradescope export,
- parse notebook answers by question,
- generate or refine rubrics from a reference solution,
- grade at scale with OpenAI models,
- review edge cases, and
- export final results back to Gradescope and Excel.

The codebase currently targets Python notebook assignments and is organized around assignment-scoped outputs so each run is reproducible, resumable, and easy to inspect.

## Why this exists

Manual notebook grading is slow, inconsistent, and difficult to scale once classes get large. This project automates the repetitive parts of the grading loop while still keeping humans in control of:
- prompt design,
- rubric quality,
- review of low-confidence cases,
- calibration, and
- final export.

It is especially useful for courses where students submit notebooks containing a mix of code, markdown explanations, outputs, and plots.

## Core capabilities

- Gather notebook submissions from a Gradescope ZIP export or extracted folder.
- Parse solution and student notebooks into normalized per-question JSON.
- Generate rubrics from the reference solution, with an optional rubric review pass.
- Grade question groups with structured JSON validation and retry logic.
- Resume grading from saved results instead of starting over.
- Regrade a subset of questions with grade_only and merge results back in.
- Flag score outliers through a calibration pass.
- Export Gradescope JSON, Excel gradebooks, and optional autograder ZIP artifacts.
- Run either from the CLI or through a FastAPI web interface.

## End-to-end workflow

The default grading path is:

parse -> grade -> export

Optional steps can be added before or after that path:

gather -> parse -> generate-rubrics -> grade -> calibrate -> export

### 1. Gather

Reads a Gradescope export and copies student notebooks into a normalized submissions directory.

### 2. Parse

Parses the reference notebook and each student notebook into structured question-level data using configurable section and question regex patterns.

### 3. Generate rubrics

Builds question rubrics from the reference solution. An optional review pass can make the rubric less brittle when the original solution contains incidental implementation choices.

### 4. Grade

Sends grouped questions to the model, validates the returned JSON, retries malformed responses, and incrementally writes results so interrupted runs can resume.

### 5. Calibrate

Computes simple score distribution statistics and flags outliers for manual review.

### 6. Export

Writes final artifacts for Gradescope and staff workflows, including JSON, Excel, and optional autograder packaging.

## Repository layout

Main pipeline modules:

- main.py: CLI entrypoint that writes config and runs selected pipeline stages.
- app.py: FastAPI backend plus static UI serving.
- gather.py: submission extraction and normalization.
- parse_notebook.py: notebook parser for solution and student files.
- rubric/ package: rubric generation and review (`python -m rubric` or import `generate_rubrics`).
- grade.py: single-student grading logic.
- batch_grader.py: sequential and parallel batch orchestration.
- calibrate.py: outlier detection on graded results.
- export.py: Gradescope and Excel export, plus autograder ZIP creation.
- linter_export.py: packaging for a notebook-format linter autograder.
- estimate.py: token and cost estimation helpers.
- prompt_builder.py: prompt construction, sanitization, and JSON extraction.
- grading_models.py: shared grading schemas and validation models.
- utils.py: config I/O, assignment-scoped logging, OpenAI client setup, and shared helpers.
- config_models.py: AppConfig, ParsingConfig, GradingConfig, default_config, normalize_qid.
- grading_helpers.py: grade_only filtering, effective_groups, needs_merge (re-exported by utils).
- pipeline_runner.py: thin wrappers for app pipeline steps (run_gather, run_parse, run_export, etc.).
- results_store.py: load_results, save_results, update_student, load_results_with_backup.

Supporting directories:

- tests/: unit and integration tests.
- ui/: browser-based setup, grading, and export interface.
- docs/: supporting documentation — see `docs/CODEBASE_GUIDE.md` (pipeline reference), `docs/OPENAI_VISION_MODELS.md` (models, pricing, HW1 variance study), `docs/AUTOGRADER_DESIGN_REVIEW.md` (design review + refactoring playbook), `docs/BUG_REPORT.md` (engineering tracker).
- output/: assignment-scoped runtime artifacts.

## Output model

The project uses an output-first assignment layout.

Root-level example config:
- `config.yaml` at the project root is only an example template. The app does not treat it as the active config source.

Assignment runtime data:
- output/{assignment_name}/config.yaml
- output/{assignment_name}/submissions/
- output/{assignment_name}/parsed/
- output/{assignment_name}/solution_parsed.json
- output/{assignment_name}/graded_results.json
- output/{assignment_name}/calibration_report.json
- output/{assignment_name}/gradescope/
- output/{assignment_name}/Final_Grades.xlsx
- output/{assignment_name}/autograder.log

This separation matters because switching assignments should move both artifacts and logs with the active assignment.

The single source of truth for a live assignment is always `output/{assignment_name}/config.yaml`.

## Requirements

- Python 3.10 or newer is recommended.
- Access to an OpenAI Developer Platform project and an API key.
- A reference notebook for the assignment.
- A Gradescope ZIP export or an already extracted submissions folder.

Dependencies are listed in requirements.txt and include FastAPI, Pydantic, pandas, openpyxl, tiktoken, and the OpenAI Python SDK.

## Installation

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "key=sk-..." > .env
```

API key lookup order:

- .env entry named key
- OPENAI_API_KEY environment variable

## Quick start

### Option A: run the CLI pipeline

The default `main.py` flow builds a config object, writes the assignment config, and runs:

parse -> grade -> export

Example commands:

```bash
python main.py
python main.py --zip gradescope_export.zip
python main.py --steps parse generate-rubrics grade export
python main.py --config-only
python main.py --model gpt-4.1-mini
python main.py --solution path/to/solution.ipynb
python main.py --submissions-dir path/to/submissions
python main.py --config output/My_Assignment/config.yaml
python main.py --no-write-config
python main.py --steps parse --config output/LabTest_3_S26/config.yaml --no-write-config
```

Notes:

- If `--config` is omitted, `main.py` writes to `output/{assignment_name}/config.yaml` based on the programmatic `CONFIG` object (from `default_config()` in config_models.py).
- If you pass `--config`, it should point to an assignment config file, typically under `output/{assignment_name}/config.yaml`.
- Programmatic callers should construct an `AppConfig` or config dict directly before invoking pipeline functions.

Supported pipeline step names:

- gather
- parse
- generate-rubrics
- grade
- calibrate
- export

### Assignment config safety (important)

If you keep hand-edited rubrics inside `output/{assignment_name}/config.yaml`, avoid rewriting that file unless you intend to.

Recommended pattern for existing assignment configs:

```bash
python main.py --steps parse grade export --config output/LabTest_3_S26/config.yaml --no-write-config
```

Use `--no-write-config` when you want to run pipeline steps against the current assignment config as-is.

It is also a good idea to keep a dated local backup before major reruns, for example:

```bash
cp output/LabTest_3_S26/config.yaml output/LabTest_3_S26/config.backup_YYYY-MM-DD.yaml
```

### Option B: run the web app

The Setup tab now starts with an assignment name and a `Load / Create` action.

- If `output/{assignment_name}/config.yaml` already exists, the UI loads it.
- If it does not exist, the UI creates the assignment folder and writes a default config there.
- After that, all edits in the UI save back to that assignment config.

The project-root `config.yaml` is not used by the UI runtime.

```bash
uvicorn app:app --reload
```

Then open http://127.0.0.1:8000.

The UI is organized around the same staff workflow:

1. Setup
2. Gather
3. Parse
4. Rubrics
5. Grade
6. Review
7. Export

## Configuration guide

Important fields in config.yaml:

- assignment_name: the assignment identifier used to scope output.
- model: grading model. For reproducible scores across regrades, use **gpt-4.1** or **gpt-4.1-mini** (temperature=0). gpt-5 models use temperature=1 and can vary significantly between runs. See `docs/OPENAI_VISION_MODELS.md` for model comparison and stability data.
- rubric_model: optional rubric-generation model; falls back to model when empty.
- solution_notebook: path to the reference notebook.
- workers: parallel grading worker count.
- max_prompt_tokens: scales per-field character caps for grading prompts (code/output/markdown truncation).
- max_completion_tokens: completion budget.
- include_reference_in_grading: whether to embed the reference solution in grading prompts.
- rubric_review: whether to run the rubric review pass.
- parsing.section_regex: regex used to identify sections.
- parsing.question_regex: regex used to identify question IDs and points.
- parsing.keep_images: whether image payloads are preserved during parsing.
- grading.question_groups: question groupings for each grading call.
- grading.grade_only: optional subset of questions for partial regrading.
- grading.grade_only_merge: whether partial grading should merge into saved results.
- gradescope_title_mapping: optional mapping from question ID to display name in Gradescope output.
- rubrics: optional question rubric map.

In practice, the most important configuration work is getting three things right:

- notebook question regexes,
- grading question groups, and
- prompt quality (edit `prompts/{assignment_name}/*.md` or `prompts/DEFAULT/*.md`; prompts are loaded from the filesystem, not stored in config).

## Direct module entrypoints

Each stage can also be run independently.

```bash
python gather.py --zip gradescope_export.zip
python gather.py --folder extracted_export_folder
python parse_notebook.py --config output/<assignment_name>/config.yaml
python -m rubric --config output/<assignment_name>/config.yaml
python grade.py --config output/<assignment_name>/config.yaml
python calibrate.py --config output/<assignment_name>/config.yaml
python export.py --config output/<assignment_name>/config.yaml
python export.py --config output/<assignment_name>/config.yaml --autograder-zip
```

For direct module entrypoints, always pass an assignment-scoped config path under
`output/{assignment_name}/config.yaml`.

## Web API summary

Configuration:

- GET /config
- PUT /config
- GET /config/default

Setup helpers:

- POST /parse-solution-upload
- POST /parse-solution

Pipeline:

- POST /gather
- POST /gather-from-folder
- POST /parse
- GET /generate-rubrics
- POST /generate-rubrics
- GET /grade/status
- GET /grade
- POST /grade/{student_name}
- POST /calibrate
- POST /export

Rubrics and estimates:

- GET /rubrics
- PUT /rubrics
- GET /estimate/rubrics
- GET /estimate/grade
- GET /estimate/grade/{student_name}

Results and parsed data:

- GET /results
- PUT /results/{student_name}
- GET /parsed/{student_name}
- GET /calibration

Downloads:

- GET /export/excel
- GET /export/autograder-zip
- GET /export/linter-zip

Static UI:

- GET /
- GET /ui/{path}

## Testing

Run the full test suite with:

```bash
.venv/bin/python -m pytest tests/ -q
```

Useful focused runs while editing:

```bash
.venv/bin/python -m pytest tests/test_app.py -q
.venv/bin/python -m pytest tests/test_grade.py -q
```

The test suite covers parsing, grading, rubric generation, export paths, utilities, and the FastAPI app.

## Safety and grading guardrails

- Student notebook content is treated as untrusted input.
- Prompt-injection boundaries and sanitizer behavior should remain intact.
- Grading responses are JSON-validated before use.
- Malformed or partial model output is retried.
- Empty submissions are normalized to a no-submission state.
- Re-running grading resumes from the saved graded_results.json unless an explicit regrade path is used.
- Logs are written to the active assignment's output directory, not a shared global log.

## Current scope and limitations

- The project is designed around Python notebook grading, not arbitrary programming languages.
- Parsing quality depends on the assignment's section and question regex patterns.
- Rubric quality depends on both the reference notebook and the rubric-generation prompt.
- Calibration is meant for review support, not as a replacement for grader judgment.
- For high-stakes use, teams should still spot-check graded results before release.

## Known engineering limitations

- `gather.py` (standalone CLI) still allows running without `--config` and defaults `--out` to `output/submissions`; for assignment-scoped runs, pass `--config output/{assignment_name}/config.yaml`.
