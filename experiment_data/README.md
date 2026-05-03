# Experiment data (local)

Course exports, manual grades, and IRR / score-comparison material. The repo **gitignores** everything in this folder except this README — all PII stays on disk.

## Canonical lab layout (every cohort, every lab is identical)

After running [`scripts/build_experiment_layout.py`](../scripts/build_experiment_layout.py):

```text
experiment_data/<cohort>/<lab>/
  _raw/                    untouched original files (created on first run)
  config.yaml              ready for the autograder pipeline
  solution.ipynb           rewritten so the default Q-style regex parses every prompt
  human_grades.csv         anon_id, Total Score, Max Points, then qid columns (e.g. 1.1, 1.2, …)
  submissions/
    001.ipynb              one notebook per anonymous stem
    002.ipynb
    ...
```

- **`anon_id` matches the submission filename stem.** Per-question rows in `human_grades.csv` and per-stem `parsed/<NNN>.json` (after parse) line up directly — no join table needed.
- **Question IDs are uniform per lab.** When the source CSV has a clean `<sec>.<qnum>` for every column we keep it; otherwise we synthesize a flat `1.<idx>` scheme based on CSV column order. Either way, CSV qids and notebook qids match exactly so AI vs human comparison joins on `(anon_id, qid)`.
- **S25 prompt rewriting:** the original notebooks use a `- N <font …> [N pts]` style with no section number. The build script injects a leading `Q<sec>.<qnum> [<pts> PTS]` line at the top of each prompt cell using `grades.csv` as the source of truth, so the **default** `parse_notebook` regex (from [`config_models.ParsingConfig`](../config_models.py)) parses every cell.
- **CSV / notebook count mismatch:** when the solution has fewer dash prompts than the CSV has question columns (or vice versa), the script warns and truncates to the smaller count so the canonical layout stays internally consistent.

### Defaults inside `config.yaml`

```yaml
assignment_name: S25_LabTest_2          # or S26_LabTest_2, etc.
model: gpt-4
solution_notebook: solution.ipynb
workers: 32
parsing:
  section_regex: '(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b'
  question_regex: '(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]'
  keep_images: true
grading:
  question_groups: []   # replaced per lab with explicit lists; see note below
  grade_only: null
  grade_only_merge: false
rubrics: {}
```

You normally do not need to edit parsing regex — every cohort uses the same defaults because the build script makes the data conform. **`grading.question_groups` must list every solution question.** For labs where the notebook has **multiple parsed sections**, each inner list is **one section** (all questions in that section, in parse order). For **single-section** labs (synthetic flat `1.<idx>` numbering), there is no section structure to follow, so groups are **fixed-size chunks** (six questions per call) only to keep prompts bounded. The checked-in canonical `config.yaml` files under each lab folder define these groups.


## Workflow

```bash
# 1. (One-time) drop raw files into the cohort folder, e.g.:
#    experiment_data/S25/LabTest_2/{LabTest_2_S25_sol.ipynb, grades.csv, metadata.yml, submissions/NNN.ipynb}
#    experiment_data/S26/{lt1.csv, lt1.ipynb, lt1.zip}   # LabTest_1
#    experiment_data/S26/{lt2.csv, lt2.ipynb, lt2.zip}   # LabTest_2

# 2. Build the canonical layout (idempotent; moves originals to _raw/ on first run):
python scripts/build_experiment_layout.py

# 3. Stage every lab into output/<assignment_name>/ (symlink submissions):
python scripts/prepare_assignment.py --all

# Or stage one lab:
python scripts/prepare_assignment.py --lab-dir experiment_data/S25/LabTest_2
# Or stage one S26 lab (after build produced ``experiment_data/S26/LabTest_N/``):
python scripts/prepare_assignment.py --lab-dir experiment_data/S26/LabTest_1

# 3b. (Optional) Structural rubric audit on staged `output/<lab>/config.yaml` (works while `output/` is gitignored):
python scripts/audit_experiment_output_rubrics.py
# Or one lab: ``python scripts/audit_experiment_output_rubrics.py --lab S25_LabTest_4``

# 4. Drive the pipeline directly from the staged config (rubrics already in `config.yaml` for experiments):
python main.py --steps parse grade --config output/S25_LabTest_2/config.yaml --no-write-config

# 5. Batch all staged labs × several models (before each (model, lab) run, moves non-empty
#    `graded_results.json` to `output/<lab>/experiment_runs/<tag>/` where `<tag>` comes from
#    `_provenance.model` in that file, not the incoming model name):
python scripts/run_experiment_grading.py --models gpt-4.1,gpt-4.1-mini,gpt-5,gpt-5-mini --workers 32

# 6. Compare archived AI scores to `human_grades.csv` (join on `anon_id` == graded `student_name`):
python scripts/compare_experiment_to_human.py --model-tag gpt-4.1-mini
```

`scripts/prepare_assignment.py` symlinks `output/<assignment_name>/submissions` to the canonical `submissions/` and writes a runtime `config.yaml` with the absolute solution path. Pass `--copy` if your filesystem cannot symlink. Re-running it overwrites prior staging.

`scripts/run_experiment_grading.py` uses the same `run_parse` / `grade_all_students` stack as `main.py` (parallel workers, resume within a run). It skips a (model, lab) when `experiment_runs/<sanitize(model)>/graded_results.json` is already complete (every `parsed/*.json` stem has a graded row and `_provenance.model` matches that model). A partial archive is copied to the root, graded with resume, then copied back and the root file removed. Otherwise it moves any other non-empty root `graded_results.json` into a provenance-named folder under `experiment_runs/` before the next run. `scripts/compare_experiment_to_human.py` writes `experiment_analysis/<model_tag>/summary.json` and `per_lab.csv` (gitignored). To refresh the tracked paper tables under `research/paper/`, run `python scripts/export_research_paper_metrics.py` (reads those summaries when present, else `research/paper/metrics_snapshot.json`).

### Discrepancy analysis report

For exploratory **human vs AI** disagreement tables (human zero / AI high, human high / AI zero, total-score MAE by human-score quartile, and a fixed case study on LabTest 7 Q1.21), run:

```bash
python scripts/analyze_human_ai_discrepancies.py --model-tag gpt-4.1 --output experiment_analysis/human_ai_discrepancies.md
```

The Markdown report’s Task 1 section documents missing historical trees (e.g. S23/S24) and, when `experiment_data/**/_raw/**/*.csv` exists, scans raw export headers for grader-like columns (Gradescope **Autograder** point columns are skipped; they are not human grader metadata). **Per-submission CA / grader identity is not in** canonical `human_grades.csv` (only `anon_id` and scores); stratifying by grader needs those raw columns or an external join.

## Cohort notes

| Cohort | Lab(s) | Students | Question count | Source layout |
|--------|--------|----------|-----------------|---------------|
| S25 | LabTest_2 | 17 | 42 (natural `<sec>.<qnum>` from CSV) | `_raw/{solution.ipynb, grades.csv, metadata.yml, submissions/}` |
| S25 | LabTest_3 / 4 | 17 / 18 | 20 / 18 (natural) | same |
| S25 | LabTest_5 / 6 / 7 | 17 / 17 / 16 | synthesized flat `1.<idx>` | same; build warns where solution prompts and CSV columns disagree |
| S26 | LabTest_1 | (after you add data) | (from CSV) | `lt1.csv`, `lt1.ipynb`, `lt1.zip` at `experiment_data/S26/` → `LabTest_1/_raw/` |
| S26 | LabTest_2 | 23 | 42 (natural) | `lt2.*` at `experiment_data/S26/` → `_raw/`; ZIP re-keyed by Submission ID |

S26 raw files use names **`ltN.csv`**, **`ltN.ipynb`**, **`ltN.zip`** at **`experiment_data/S26/`** (same level as `LabTest_N/`). The build script discovers every `N` that has **`ltN.zip`** at the cohort root (or an existing **`LabTest_N/_raw/submissions.zip`**) and writes **`experiment_data/S26/LabTest_N/`** with `assignment_name: S26_LabTest_N`.

## Re-running the build

The build script is idempotent: it always reads from `_raw/` and overwrites the canonical files at lab root. If you replace any raw file, just rerun `python scripts/build_experiment_layout.py`. To re-do staging for the pipeline, rerun `python scripts/prepare_assignment.py --lab-dir <lab>`.

## Privacy / git hygiene

The root `.gitignore` covers `experiment_data/**` with an exception only for this README. The `_raw/` folders contain Gradescope exports with names, SID, and email — they must stay local. The canonical `human_grades.csv` only carries `anon_id`, totals, and per-qid scores, but is still gitignored by default; commit anonymized derivatives explicitly only when you have reviewed them.

If you keep a local `.cursorignore` (the repo-level copy is gitignored), exclude `experiment_data/**/*.{csv,ipynb,zip}` so Cursor does not pull large or sensitive artifacts into AI context.

**Staging (`output/S25_LabTest_*`, `output/S26_LabTest_2/`):** The root `.gitignore` ignores all of `output/`, so Cursor’s file indexer and **readonly** subagents usually cannot open staged `config.yaml` there. Use a terminal (`python` / `cat`) for audits, or temporarily relax ignores. In `.cursorignore`, avoid a bare `LabTest*/` rule: some matchers treat it like a substring and hide `output/S25_LabTest_*` paths; prefer `/LabTest*/` (repo root only) plus explicit `output/**/parsed/` style ignores for bulk under `output/`.

## Tests

[`tests/test_experiment_data_scripts.py`](../tests/test_experiment_data_scripts.py) covers question-column parsing (natural vs synthesized), the S25 prompt rewriter, and the end-to-end S25 / S26 builders plus `prepare_assignment` against synthetic fixtures.
