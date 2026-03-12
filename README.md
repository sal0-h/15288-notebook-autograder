# AI Autograder for Gradescope

AI-assisted grading pipeline for Jupyter notebook assignments.

It parses solution and student notebooks, grades with an OpenAI model, and exports:
- Gradescope JSON results
- Excel gradebook
- Optional Gradescope autograder ZIP

Built for CMU Qatar courses (15-288).

## Table of Contents

1. Overview
2. Architecture
3. Pipeline
4. Configuration
5. CLI Usage
6. Web UI and API
7. Testing
8. Notes and Guardrails

## Overview

Core capabilities:
- Gather student notebooks from a Gradescope export (ZIP or extracted folder)
- Parse notebooks into structured JSON per question
- Optionally generate rubrics from the solution notebook
- Grade by question groups with retry + validation
- Optionally calibrate (z-score outlier detection)
- Export grades to Gradescope JSON and Excel

Default pipeline from `main.py` is:
- `parse -> grade -> export`

Optional steps:
- `gather`
- `generate-rubrics`
- `calibrate`

## Architecture

Current grading stack is split into focused modules:
- `grading_models.py`: Pydantic grading models and shared constants
- `prompt_builder.py`: token estimation, JSON extraction, sanitization, prompt construction
- `grade.py`: per-group and per-student grading logic
- `batch_grader.py`: batch orchestration (sequential/parallel, resume support)

Main project files:
- `main.py`: CLI pipeline entrypoint
- `app.py`: FastAPI backend + static UI serving
- `gather.py`: submission extraction and normalization
- `parse_notebook.py`: notebook parser
- `rubric.py`: rubric generation and optional rubric review pass
- `calibrate.py`: outlier detection
- `export.py`: Gradescope + Excel export, autograder ZIP build
- `estimate.py`: cost and token estimates
- `linter_export.py`: linter autograder ZIP
- `utils.py`: config loading/saving, assignment-scoped logging, shared grade_only helpers, OpenAI client, shared validation models

Output layout is assignment-scoped:
- `output/{assignment_name}/submissions/`
- `output/{assignment_name}/parsed/`
- `output/{assignment_name}/solution_parsed.json`
- `output/{assignment_name}/graded_results.json`
- `output/{assignment_name}/calibration_report.json`
- `output/{assignment_name}/gradescope/*.json`
- `output/{assignment_name}/Final_Grades.xlsx`
- `output/{assignment_name}/autograder.log`

## Pipeline

### 1) Gather

Input:
- Gradescope export ZIP, or extracted export folder

Output:
- Notebook files in `submissions_dir`

What it does:
- Reads Gradescope metadata
- Finds each student notebook
- Copies with normalized names

### 2) Parse

Input:
- Solution notebook (`solution_notebook`)
- Student notebooks from `submissions_dir`

Output:
- `solution_parsed.json`
- Per-student JSON files in `parsed_dir`

What it does:
- Detects sections/questions via regex from config
- Extracts code, markdown, text outputs, and images
- Produces normalized per-question payloads used by grading

### 3) Generate Rubrics (optional)

Input:
- `solution_parsed.json`

Output:
- `rubrics` saved in assignment config

What it does:
- Generates rubric items per question via LLM
- Optional review pass (`rubric_review`) can soften over-specific criteria while preserving deductions/points

### 4) Grade

Input:
- Parsed solution + parsed student files
- Question groups from `grading.question_groups`

Output:
- `graded_results.json` (incrementally updated)

What it does:
- Grades per group with validated JSON responses
- Retries malformed/partial LLM responses
- Supports `grade_only` filtering
- Supports resume (already graded students are skipped)
- Supports optional `grade_only_merge` behavior used by API regrading paths

### 5) Calibrate (optional)

Input:
- `graded_results.json`

Output:
- `calibration_report.json`

What it does:
- Computes per-question score distribution
- Flags outliers by z-score

### 6) Export

Input:
- `graded_results.json`

Output:
- `gradescope/*.json`
- `Final_Grades.xlsx`
- Optional `gradescope_autograder.zip`
- Optional `linter_autograder.zip`

## Configuration

The project uses an output-first config layout:
- Root `config.yaml` keeps assignment pointer + root-level controls (`prompts`, `rubric_review`, `include_reference_in_grading`)
- Assignment runtime config is stored at `output/{assignment_name}/config.yaml`

Key fields:
- `assignment_name`: assignment identifier; output is scoped under this name
- `model`: grading model
- `rubric_model`: rubric generation model (falls back to `model` when empty)
- `solution_notebook`: path to solution notebook
- `workers`: grading worker count
- `max_prompt_tokens`: prompt token budget
- `max_completion_tokens`: completion token budget
- `rubric_review`: enable rubric review pass
- `include_reference_in_grading`: include reference solution in grading prompts
- `parsing.section_regex`: section matcher
- `parsing.question_regex`: question matcher
- `parsing.keep_images`: include parsed image payloads
- `grading.question_groups`: grouped question IDs for each grading call
- `grading.grade_only`: optional subset of question IDs to grade
- `grading.grade_only_merge`: when true, grade only the selected questions and merge those results into existing saved grades
- `rubrics`: optional per-question rubric map
- `prompts.system`: grading system prompt
- `prompts.rubric_system`: rubric generation system prompt
- `upload_max_mb`: max ZIP upload size for `/gather` (default 500)

## CLI Usage

### Setup

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
echo "key=sk-..." > .env
```

### Main pipeline (`main.py`)

```bash
# Default: parse + grade + export
python main.py

# Add gather by supplying a Gradescope ZIP
python main.py --zip gradescope_export.zip

# Only write config
python main.py --config-only

# Explicit steps
python main.py --steps parse
python main.py --steps parse generate-rubrics grade calibrate export

# Overrides
python main.py --model gpt-5-mini
python main.py --solution path/to/solution.ipynb
python main.py --submissions-dir path/to/submissions
python main.py --no-write-config
python main.py --config my_config.yaml
```

Available `--steps` values:
- `gather`
- `parse`
- `generate-rubrics`
- `grade`
- `calibrate`
- `export`

### Module entrypoints

```bash
python gather.py --zip gradescope_export.zip
python gather.py --folder extracted_export_folder
python parse_notebook.py --config config.yaml
python rubric.py --config config.yaml
python grade.py --config config.yaml
python calibrate.py --config config.yaml
python export.py --config config.yaml
python export.py --config config.yaml --autograder-zip
```

## Web UI and API

Run server:

```bash
uvicorn app:app --reload
```

Open `http://127.0.0.1:8000`.

### UI tabs

1. Setup
2. Gather
3. Parse
4. Rubrics
5. Grade
6. Review
7. Export

### API endpoints

Config:
- `GET /config`
- `PUT /config`
- `GET /config/default`

Setup helpers:
- `POST /parse-solution-upload`
- `POST /parse-solution`

Pipeline:
- `POST /gather`
- `POST /gather-from-folder`
- `POST /parse`
- `GET /generate-rubrics` (SSE stream)
- `POST /generate-rubrics` (blocking)
- `GET /grade/status`
- `GET /grade` (SSE stream)
- `POST /grade/{student_name}` (single student regrade)
- `POST /calibrate`
- `POST /export`

Rubrics and estimates:
- `GET /rubrics`
- `PUT /rubrics`
- `GET /estimate/rubrics`
- `GET /estimate/grade`
- `GET /estimate/grade/{student_name}`

Results and parsed data:
- `GET /results`
- `PUT /results/{student_name}`
- `GET /parsed/{student_name}`
- `GET /calibration`

Downloads:
- `GET /export/excel`
- `GET /export/autograder-zip`
- `GET /export/linter-zip`

Static UI:
- `GET /`
- `GET /ui/{path}`

## Testing

```bash
.venv/bin/python -m pytest tests/ -q
```

Test suite covers parsing, grading, rubric generation, API behavior, export paths, and utilities.

## Notes and Guardrails

- API key lookup order:
  - `.env` key: `key=...`
  - fallback: `OPENAI_API_KEY`
- Both the CLI and web app write logs to the active assignment's `output/{assignment_name}/autograder.log`.
- Student submission content is treated as untrusted input and sanitized before prompt injection into model messages.
- Grading responses are JSON-validated; malformed responses trigger retries.
- Empty/missing submissions are normalized to `[no submission]`.
- Re-running grading resumes from existing `graded_results.json` unless regrade endpoints are used.
