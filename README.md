# AI Autograder for Gradescope

An AI-powered grading pipeline for Jupyter notebook assignments. Parses student submissions and reference solutions, sends them to an LLM (OpenAI) for evaluation, and exports results in both Gradescope autograder format and human-readable Excel.

Built for Carnegie Mellon University in Qatar courses (15-288, 07-280).

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [The Pipeline](#the-pipeline)
3. [How Everything Works](#how-everything-works)
4. [Configuration](#configuration)
5. [Usage](#usage)
6. [Web UI](#web-ui)
7. [Testing](#testing)

---

## Project Structure

```
ai_autograder/
├── main.py              # CLI entry point: writes config, runs pipeline
├── config.yaml          # Assignment config (auto-generated or edited)
├── utils.py             # Shared: load_config, save_config, get_openai_client, Pydantic models
├── gather.py            # Step 1: Extract notebooks from Gradescope ZIP
├── parse_notebook.py    # Step 2: Parse notebooks into structured JSON
├── rubric.py            # Step 3: LLM-based rubric generation from solution
├── grade.py             # Step 4: LLM grading engine
├── calibrate.py         # Step 5: Post-grading outlier detection (z-score)
├── export.py            # Step 6: Export to Gradescope JSON + Excel
├── app.py               # FastAPI backend
├── ui/
│   └── index.html       # Single-page web UI
├── tests/
│   ├── conftest.py
│   ├── test_grade.py    # Tests for grade.py
│   ├── test_parse.py    # Tests for parse_notebook.py
│   ├── test_utils.py    # Tests for utils.py
│   ├── test_gather.py   # Tests for gather.py
│   ├── test_app.py      # Tests for app.py API
│   └── test_export.py   # Tests for export.py
├── requirements.txt
├── .env                 # API key (key=...)
└── output/              # Generated (gitignored)
    └── {assignment_name}/   # Per-assignment output (e.g. LabTest_2_S26)
        ├── submissions/     # Raw notebooks after gather
        ├── parsed/          # Parsed JSON per student
        ├── solution_parsed.json
        ├── graded_results.json
        ├── calibration_report.json
        ├── gradescope/      # Per-student Gradescope JSON
        └── Final_Grades.xlsx
```

---

## The Pipeline

The pipeline has six steps:

```
┌──────────┐   ┌──────────┐   ┌──────────────────┐   ┌──────────┐   ┌───────────┐   ┌──────────┐
│ 1.GATHER │   │ 2.PARSE  │   │ 3.GEN-RUBRICS    │   │ 4.GRADE  │   │5.CALIBRATE│   │ 6.EXPORT │
│          │   │          │   │                  │   │          │   │           │   │          │
│Gradescope│──▶│Notebooks │──▶│LLM-generated     │──▶│LLM eval  │──▶│Outlier    │──▶│Gradescope│
│ZIP       │   │→ JSON    │   │rubrics per Q     │   │per student│   │detection  │   │JSON+Excel│
└──────────┘   └──────────┘   └──────────────────┘   └──────────┘   └───────────┘   └──────────┘
```

Steps 3 (generate-rubrics) and 5 (calibrate) are optional — the default CLI run is `parse → grade → export`.

---

### Step 1: Gather

**Input:** Gradescope export ZIP (or extracted folder)

**Output:** `output/{assignment_name}/submissions/` with one `.ipynb` per student, named `{StudentName}_{original}.ipynb`

**What it does:**

1. Extracts the ZIP to a temp directory
2. Finds `submission_metadata.yml` (Gradescope metadata)
3. For each submission folder: reads student name from metadata, finds the `.ipynb`, copies it to `output/submissions/` with a sanitized filename
4. Reports status: `ok`, `missing`, or `duplicate`

**Requirements:** Gradescope export must contain `submission_metadata.yml` and per-submission folders with `.ipynb` files.

---

### Step 2: Parse

**Input:** `output/{assignment_name}/submissions/*.ipynb` + solution notebook (path in config)

**Output:** `output/{assignment_name}/parsed/{StudentName}.json` + `output/{assignment_name}/solution_parsed.json`

**What it does:**

1. Parses the solution notebook first
2. Writes `solution_parsed.json` to `output/{assignment_name}/`
3. Parses each student notebook
4. Writes `{StudentName}.json` to `output/{assignment_name}/parsed/`
5. Returns a verification report: which questions were found, which are missing

**Parsing logic:**

- Uses regex from config to detect **sections** (e.g. `# <font color='red'>1 Read and inspect the data</font>`) and **questions** (e.g. `- Q1.1 [1 PTS] ...`)
- For each question, collects all cells until the next section/question:
  - **Code cells:** code, text output, images (Base64)
  - **Markdown cells:** treated as written answers
- Produces per-question: `answer_code_concat`, `answer_text_concat`, `answer_markdown_concat`, `answer_cells` (with images)

**Parsed JSON structure:**

```json
{
  "sections": {
    "1": {
      "overview_markdown": "# Section 1...",
      "questions": {
        "1.1": {
          "points": 1,
          "question_markdown": "- Q1.1 [1 PTS] ...",
          "answer_cells": [
            { "code": "...", "output_text": "...", "images": [...] }
          ],
          "answer_code_concat": "...",
          "answer_text_concat": "...",
          "answer_markdown_concat": "..."
        }
      }
    }
  }
}
```

---

### Step 3: Generate Rubrics (optional)

**Input:** `output/{assignment_name}/solution_parsed.json`

**Output:** `rubrics` key written to `config.yaml`

**What it does:**

For each question group, sends the question text and reference solution to the LLM and asks it to produce grading criteria. The criteria are stored in `config.yaml` under `rubrics` and are injected into the grading prompt in Step 4.

**Rubric format (per question in config):**

```yaml
rubrics:
  "1.1":
    points: 2
    criteria: "Full marks: correct plot with labeled axes. -1: axes unlabeled. Zero: wrong data or blank."
```

Rubrics are optional — if absent, grading proceeds without pre-defined criteria.

---

### Step 4: Grade

**Input:** `output/{assignment_name}/parsed/*.json` + `output/{assignment_name}/solution_parsed.json`

**Output:** `output/{assignment_name}/graded_results.json` (updated incrementally after each student)

**What it does:**

1. Loads `solution_parsed.json`
2. For each student in `output/{assignment_name}/parsed/`:
   - Skips if already in `graded_results.json` (resume support)
   - For each **question group** in config:
     - Builds a prompt with: question text, rubric (if set), reference solution, student submission
     - Sends to OpenAI API (with vision support for images)
     - Parses JSON response, validates with Pydantic
     - Retries up to 2 times if response is partial or malformed
   - Aggregates scores and feedback
   - Appends to `graded_results.json` immediately (incremental save)

**Question groups:**

Questions are graded in groups (e.g. `["1.1", "1.2", "1.3"]`) so the LLM sees related context. Each group is one API call.

**Grade only specific questions:**

Set `grading.grade_only: ['1.1', '2.1', '4.2']` in config to grade only those questions. All others receive 0 and feedback `[skipped - not in grade_only]`. `total_max` still includes all questions. Omit `grade_only` to grade everything.

**LLM prompt structure (per group):**

```
[system prompt with grading guidelines]

--- QUESTION 1.1 (1 pts) ---
<question text>
RUBRIC:
<rubric criteria if set>

REFERENCE SOLUTION:
Code:
<solution code>
Output:
<solution output>
[2 reference plot(s) follow below]
<images>

STUDENT SUBMISSION:
<<<STUDENT_SUBMISSION>>>
Code:
<student code>
...
<<<END_STUDENT_SUBMISSION>>>
```

**Output format:**

```json
[
  {
    "student_name": "Alice",
    "questions": {
      "1.1": {
        "score": 1,
        "max": 1,
        "feedback": "",
        "confidence": "high",
        "requires_review": false
      },
      "1.2": {
        "score": 0.5,
        "max": 1,
        "feedback": "-0.5 (missing axis label)",
        "confidence": "medium",
        "requires_review": false
      }
    },
    "total_score": 28.5,
    "total_max": 35,
    "summary_feedback": "Q1.2: -0.5 (missing axis label). Q4.1: -1 (wrong value)."
  }
]
```

- `summary_feedback` only includes deductions (not full-marks questions).
- `confidence`: `"high"`, `"medium"`, or `"low"` — the LLM's self-reported confidence.
- `requires_review`: `true` when the LLM flags the answer as ambiguous or uninterpretable (e.g. answer is only an image).

---

### Step 5: Calibrate (optional)

**Input:** `output/{assignment_name}/graded_results.json`

**Output:** `output/{assignment_name}/calibration_report.json`

**What it does:**

Computes the mean and standard deviation of scores per question across all students. Flags any student-question pair where the z-score exceeds ±2 (i.e. an outlier relative to the class). These are surfaced in the Review tab as potential grading errors.

**Calibration report format:**

```json
[
  {
    "student_name": "Bob",
    "qid": "3.1",
    "score": 0,
    "max": 3,
    "mean": 2.4,
    "std": 0.5,
    "z_score": -4.8,
    "flag_reason": "low"
  }
]
```

---

### Step 6: Export

**Input:** `output/{assignment_name}/graded_results.json`

**Output:**

- `output/{assignment_name}/gradescope/{StudentName}.json` — Gradescope autograder format
- `output/{assignment_name}/Final_Grades.xlsx` — Human-readable spreadsheet

**Gradescope JSON format:**

```json
{
  "tests": [
    {
      "name": "Q1.1",
      "score": 1,
      "max_score": 1,
      "output": "",
      "visibility": "visible"
    }
  ]
}
```

**Excel:** Columns: `student_name`, `total_score`, `Q1.1`, `Q1.2`, ..., `summary_feedback`.

---

## How Everything Works

### Config and Path Resolution

- **Config file:** `config.yaml` (default). Paths in config are resolved **relative to the config file's directory**, not the current working directory.
- **Example:** If `config.yaml` is at `/home/project/config.yaml` and `solution_notebook: "archive1/sol.ipynb"`, it resolves to `/home/project/archive1/sol.ipynb`.

### Per-assignment output directories

All outputs are scoped under `output_dir/{assignment_name}/`. This lets you grade multiple assignments without overwriting results:

- **LabTest_2_S26** → `output/LabTest_2_S26/submissions/`, `parsed/`, `graded_results.json`, etc.
- **LabTest_3_S26** → `output/LabTest_3_S26/...`

To grade a different assignment, change `assignment_name` in `config.yaml` (and `solution_notebook` if needed), then run the pipeline. Each assignment keeps its own outputs.

### API Key

- Stored in `.env` as `key=your-openai-api-key`
- Loaded via `python-dotenv`; passed explicitly to `OpenAI(api_key=...)`

### LLM Response Validation

- **Pydantic models:** `QuestionGrade` (score, feedback, confidence, requires_review) and `GradingResponse` (grades dict)
- **Key normalization:** `Q4.1` and `4.1` both map to `4.1`
- **Retry:** If any question has placeholder feedback (`[not returned by LLM]` or `[parse error in LLM response]`), the group is retried up to 2 times with exponential backoff
- **Temperature:** `0` for deterministic grading
- **Token limits:** `max_prompt_tokens` (default 80k), `max_completion_tokens` (default 4k). Long outputs are truncated.
- **Response format:** `{"type": "json_object"}` enforced on every API call

### Images

- Parsed images are Base64 PNG/JPEG
- Sent to the API as `image_url` blocks with `data:image/png;base64,...`
- Placed inline after the text they describe (e.g. `[2 reference plot(s) follow below]`)

### Ungrouped Questions

- If a question exists in the solution but is not in any `question_groups`, it gets score 0 and feedback `[not included in grading groups]`
- A warning is logged at the start of grading

### Resume support

- If grading is interrupted, re-running `grade` skips students already in `graded_results.json` and continues from the next one

---

## Configuration

| Key | Description |
|-----|-------------|
| `assignment_name` | Label for the assignment; all outputs go under `output_dir/{assignment_name}/` |
| `model` | OpenAI model (e.g. `gpt-4o-mini`, `gpt-4o`) |
| `solution_notebook` | Path to reference solution `.ipynb` |
| `output_dir` | Base directory; outputs go to `output_dir/{assignment_name}/submissions`, `parsed`, etc. |
| `max_prompt_tokens` | Max tokens for prompt (triggers truncation). Default: 80000 |
| `max_completion_tokens` | Max tokens for LLM response. Default: 4096 |
| `workers` | Number of parallel grading workers (default: 1). Set > 1 for faster grading of large classes. |
| `parsing.section_regex` | Regex to detect section headers |
| `parsing.question_regex` | Regex to detect questions (must capture section, question number, points) |
| `parsing.keep_images` | Whether to include Base64 images in parsed output |
| `grading.question_groups` | List of question ID lists, e.g. `[["1.1","1.2"], ["2.1"]]` |
| `grading.grade_only` | Optional list of question IDs to grade; others get 0 and feedback `[skipped - not in grade_only]`. Omit to grade all. |
| `rubrics` | Optional per-question rubrics (auto-generated or hand-edited). Dict of `{qid: {points, criteria}}`. |
| `prompts.system` | System prompt for the LLM grader |

---

## Usage

### CLI

```bash
# Create venv and install
python -m venv .venv
.venv/bin/pip install -r requirements.txt

# Set API key
echo "key=sk-..." > .env

# Run full pipeline (parse + grade + export)
python main.py

# With Gradescope ZIP (gather + parse + grade + export)
python main.py --zip gradescope_export.zip

# Only write config.yaml, don't run pipeline
python main.py --config-only

# Run specific steps
python main.py --steps parse
python main.py --steps parse generate-rubrics grade calibrate export

# Override model
python main.py --model gpt-4o

# Override solution notebook path
python main.py --solution path/to/solution.ipynb

# Override submissions directory
python main.py --submissions-dir path/to/submissions/

# Use existing config without overwriting
python main.py --no-write-config

# Custom config path
python main.py --config my_config.yaml
```

Available `--steps` choices: `gather`, `parse`, `generate-rubrics`, `grade`, `calibrate`, `export`

Default steps (when `--steps` is not given): `parse grade export`

### Individual modules

```bash
# Gather only
python gather.py --zip export.zip

# Parse only
python parse_notebook.py

# Generate rubrics only (requires solution_parsed.json)
python rubric.py

# Grade only (requires parsed output)
python grade.py

# Calibrate only (requires graded_results.json)
python calibrate.py

# Export only (requires graded_results.json)
python export.py
```

---

## Web UI

```bash
uvicorn app:app --reload
```

Open browser to `http://localhost:8000`.

**Note:** The Review tab uses [DOMPurify](https://github.com/cure53/DOMPurify) (loaded from CDN) to safely render question markdown from student submissions.

**Tabs:**

1. **Gather** — Upload Gradescope ZIP or specify a local folder path
2. **Parse** — Run parse, view verification report
3. **Rubrics** — Generate LLM rubrics from solution, edit per-question criteria, save to config
4. **Grade** — Start grading, view live progress (SSE stream)
5. **Review** — Load results, edit scores/feedback, view confidence badges and outlier flags, save
6. **Export** — Generate Gradescope JSON + Excel, download

**API endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/config` | Read current config |
| `PUT` | `/config` | Update config |
| `POST` | `/gather` | Upload ZIP and run gather |
| `POST` | `/gather-from-folder` | Run gather from a local folder path |
| `POST` | `/parse` | Run parse step |
| `POST` | `/generate-rubrics` | Generate rubrics from solution and save to config |
| `GET` | `/rubrics` | Read rubrics from config |
| `PUT` | `/rubrics` | Save edited rubrics to config |
| `GET` | `/grade` | SSE stream of grading progress |
| `POST` | `/calibrate` | Run outlier detection on graded results |
| `GET` | `/calibration` | Read saved calibration report |
| `GET` | `/results` | Read full graded results |
| `PUT` | `/results/{student}` | Update one student's scores/feedback |
| `GET` | `/parsed/{student}` | Get parsed notebook for a student |
| `POST` | `/export` | Run export step |
| `GET` | `/export/excel` | Download Final_Grades.xlsx |

---

## Testing

```bash
.venv/bin/pytest tests/ -v
```

Tests cover grading logic, JSON parsing, prompt building, notebook parsing, config loading, gather, export, and API endpoints.
