# Experiment and evaluation data (local)

This directory holds **course exports, manual scores, and evaluation artifacts** used for IRR, paper tables, and calibration. It is **not** part of the autograder runtime (`output/{assignment}/` stays the pipeline’s assignment root).

## Do not commit raw exports

Gradescope CSV exports include **names, SID, email**, and other identifiers. The repo **ignores** everything here except this `README.md` (see root `.gitignore`). Keep raw files on disk for your own analysis; if something must live in git, add only **anonymized** tables (e.g. `student_anon`, `qid`, `score`, `max`) under a path you explicitly allow, or keep them in a private store.

## Suggested layout

Use a stable cohort label (e.g. `S26`, `S25`) and assignment slug per export:

```text
experiment_data/
  README.md                 ← this file (tracked)
  S26/
    lt2.csv                 ← example: Gradescope export (ignored)
    lt2.ipynb               ← reference notebook (ignored; can be huge)
    lt2.zip                 ← bundle (ignored)
  S25/
    <labtest>/
      ...
```

Add a one-line `SOURCE.txt` or notes in your analysis doc describing **export date**, **Gradescope column → QID** mapping, and **who graded** when you add new cohorts.

## Relating to the autograder

- **Parsed notebooks / AI grades:** still under `output/{assignment_name}/parsed/` and `graded_results.json` (also gitignored by default).
- **Human ground truth:** normalize Gradescope columns (e.g. `2: 1.1 (1.0 pts)`) to canonical question IDs (`1.1`, `2.3`, …) in a script; join to AI output on an anonymous key, not on raw email.

## Naming

The project uses **`experiment_data`** (singular) at the repo root. Prefer the same name in scripts and docs so paths stay consistent.

## Cursor / IDE indexing

This repo’s root `.cursorignore` is not tracked in git (see `.gitignore`). If you use a local `.cursorignore` copy, add patterns such as `experiment_data/**/*.csv`, `experiment_data/**/*.ipynb`, and `experiment_data/**/*.zip` so large exports are not pulled into AI context by mistake.
