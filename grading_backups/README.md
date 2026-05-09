# Grading backups (local only)

This folder holds **snapshots of graded JSON** under the repo tree. Contents are **gitignored** (except this README) because they duplicate `output/` data and may include course identifiers.

Create a timestamped snapshot from the repo root:

```bash
python scripts/backup_grading_results.py
```

Optional:

```bash
python scripts/backup_grading_results.py --dest grading_backups/my_run_name
python scripts/backup_grading_results.py --include-experiment-analysis
```

Each run copies, for every assignment directory under `output/` that has grading artifacts:

- `graded_results.json` at the assignment root (if present)
- the whole `experiment_runs/` tree (if present)

With `--include-experiment-analysis`, also copies `experiment_analysis/` when it exists (compare-script outputs).

**Restore:** copy files back under `output/<assignment>/` (and `experiment_analysis/` if you backed that up). Overwrite only when you intend to roll back.

**Rubric prompt tweaks:** Changing `prompts/DEFAULT/rubric_system.md` affects **new** rubric generation only. Existing `rubrics:` in `output/*/config.yaml` stay as-is until you run `generate-rubrics` again. A full re-grade is only “worth it” if you regenerate rubrics and want scores aligned to the new criteria—not for the prompt edit alone.
