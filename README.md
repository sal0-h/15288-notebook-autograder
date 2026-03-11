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
├── grade.py             # Step 3: LLM grading engine
├── export.py            # Step 4: Export to Gradescope JSON + Excel
├── app.py               # FastAPI backend
├── ui/
│   └── index.html       # Single-page web UI
├── tests/
│   ├── test_grade.py    # Tests for grade.py
│   └── test_parse.py   # Tests for parse_notebook.py
├── requirements.txt
├── .env                 # API key (key=...)
└── output/              # Generated (gitignored)
    └── {assignment_name}/   # Per-assignment output (e.g. LabTest_2_S26)
        ├── submissions/     # Raw notebooks after gather
        ├── parsed/          # Parsed JSON per student
        ├── solution_parsed.json
        ├── graded_results.json
        ├── gradescope/      # Per-student Gradescope JSON
        └── Final_Grades.xlsx
```

---

## The Pipeline

The pipeline has four steps:

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ 1. GATHER   │    │ 2. PARSE    │    │ 3. GRADE    │    │ 4. EXPORT   │
│             │    │             │    │             │    │             │
│ Gradescope  │───▶│ Notebooks   │───▶│ LLM eval    │───▶│ Gradescope  │
│ ZIP         │    │ → JSON      │    │ per student │    │ JSON + Excel│
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

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

### Step 3: Grade

**Input:** `output/{assignment_name}/parsed/*.json` + `output/{assignment_name}/solution_parsed.json`

**Output:** `output/{assignment_name}/graded_results.json` (updated incrementally after each student)

**What it does:**

1. Loads `solution_parsed.json`
2. For each student in `output/{assignment_name}/parsed/`:
   - Skips if already in `graded_results.json` (resume support)
   - For each **question group** in config:
     - Builds a prompt with: question text, reference solution, student submission
     - Sends to OpenAI API (with vision support for images)
     - Parses JSON response, validates with Pydantic
     - Retries up to 2 times if response is partial or malformed
   - Aggregates scores and feedback
   - Appends to `graded_results.json` immediately (incremental save)

**Question groups:**

Questions are graded in groups (e.g. `["1.1", "1.2", "1.3"]`) so the LLM sees related context. Each group is one API call.

**LLM prompt structure (per group):**

```
You are grading questions 1.1, 1.2, 1.3.
Return valid JSON only: {"QID": {"score": N, "feedback": "..."}, ...}

--- QUESTION 1.1 (1 pts) ---
<question text>

REFERENCE SOLUTION:
Code:
<solution code>
Output:
<solution output>
[2 reference plot(s) follow below]
<images>

STUDENT SUBMISSION:
Code:
<student code>
...
```

**Output format:**

```json
[
  {
    "student_name": "Alice",
    "questions": {
      "1.1": { "score": 1, "max": 1, "feedback": "" },
      "1.2": { "score": 0.5, "max": 1, "feedback": "-0.5 (missing tail)" }
    },
    "total_score": 28.5,
    "total_max": 35,
    "summary_feedback": "Q1.2: -0.5 (missing tail). Q4.1: -1 (wrong value)."
  }
]
```

`summary_feedback` only includes deductions (not full-marks questions).

---

### Step 4: Export

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

- **Pydantic models:** `QuestionGrade` (score, feedback) and `GradingResponse` (grades dict)
- **Key normalization:** `Q4.1` and `4.1` both map to `4.1`
- **Retry:** If any question has placeholder feedback (`[not returned by LLM]` or `[parse error in LLM response]`), the group is retried up to 2 times with exponential backoff
- **Temperature:** `0` for deterministic grading
- **Token limits:** `max_prompt_tokens` (default 80k), `max_completion_tokens` (default 4k). Long outputs are truncated.

### Images

- Parsed images are Base64 PNG/JPEG
- Sent to the API as `image_url` blocks with `data:image/png;base64,...`
- Placed inline after the text they describe (e.g. `[2 reference plot(s) follow below]`)

### Ungrouped Questions

- If a question exists in the solution but is not in any `question_groups`, it gets score 0 and feedback `[not graded — not included in question_groups]`
- A warning is logged at the start of grading

### Resume support

- If grading is interrupted, re-running `grade` skips students already in `graded_results.json` and continues from the next one

---

## Configuration

| Key | Description |
|-----|-------------|
| `assignment_name` | Label for the assignment; all outputs go under `output_dir/{assignment_name}/` |
| `model` | OpenAI model (e.g. `gpt-5-mini`, `gpt-4o`) |
| `solution_notebook` | Path to reference solution `.ipynb` |
| `output_dir` | Base directory; outputs go to `output_dir/{assignment_name}/submissions`, `parsed`, etc. |
| `max_prompt_tokens` | Max tokens for prompt (triggers truncation) |
| `max_completion_tokens` | Max tokens for LLM response |
| `parsing.section_regex` | Regex to detect section headers |
| `parsing.question_regex` | Regex to detect questions (must capture section, question number, points) |
| `parsing.keep_images` | Whether to include Base64 images in parsed output |
| `workers` | Number of parallel grading workers (default: 1). Set > 1 for faster grading of large classes. |
| `grading.question_groups` | List of question ID lists, e.g. `[["1.1","1.2"], ["2.1"]]` |
| `prompts.system` | System prompt for the LLM |

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

# With Gradescope ZIP
python main.py --zip gradescope_export.zip

# Only parse
python main.py --steps parse

# Override model
python main.py --model gpt-4o

# Use existing config without overwriting
python main.py --no-write-config

# Custom config path
python main.py --config my_config.yaml
```

### Individual modules

```bash
# Gather only
python gather.py --zip export.zip

# Parse only
python parse_notebook.py

# Grade only (requires parsed output)
python grade.py

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

1. **Gather** — Upload Gradescope ZIP
2. **Parse** — Run parse, view verification report
3. **Grade** — Start grading, view live progress (SSE)
4. **Review** — Load results, edit scores/feedback, save
5. **Export** — Generate Gradescope JSON + Excel, download

**API endpoints:**

- `GET /config`, `PUT /config` — Read/write config
- `POST /gather` — Upload ZIP
- `POST /parse` — Run parse
- `GET /grade` — SSE stream of grading progress
- `GET /results`, `PUT /results/{student}` — Read/update graded results
- `GET /parsed/{student}` — Get parsed notebook for review
- `POST /export` — Run export
- `GET /export/excel` — Download Excel

---

## Testing

```bash
.venv/bin/pytest tests/ -v
```

Run tests for grading logic, JSON parsing, prompt building, and notebook parsing.
