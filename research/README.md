# Research artifacts

Scratch and large exports stay **gitignored** under `research/*` except the tracked **`paper/`** subtree, which holds draft tables and prose for the human–AI comparison study.

Regenerate tabular outputs after a new compare run:

```bash
python scripts/compare_experiment_to_human.py --model-tag gpt-4.1
python scripts/compare_experiment_to_human.py --model-tag gpt-4.1-mini
python scripts/export_research_paper_metrics.py --write-snapshot
```

When both `experiment_analysis/gpt-4.1/summary.json` and `experiment_analysis/gpt-4.1-mini/summary.json` exist, the exporter reads them and refreshes `research/paper/human_ai_metrics_wide.csv`. Use `--write-snapshot` to overwrite `metrics_snapshot.json` from the same summaries; if either summary is missing, the CSV falls back to the last committed snapshot.

HW1 human vs AI agreement: `python scripts/compute_irr.py --out research/paper/hw1_irr_metrics.json` (requires `scikit-learn` in the dev environment). When a second grader’s wide CSV is present locally, the script can compare Gradescope JSON exports vs that CSV (same student subsample); use `--no-grader_b-csv` for a single human track. **Do not commit** raw Gradescope JSON, hand-grade CSVs, or `hw1_irr_metrics.json`—they stay gitignored under `research/paper/` (see `.gitignore`).

**Prompt-injection red-team (HW2):** `python scripts/build_hw2_injection_bench.py` writes two folders — `output/HW2_Injection_Bench_gpt_4_1/` and `output/HW2_Injection_Bench_gpt_4_1_mini/` — each with `grading.grade_only: ['1.3', '2.1', '2.6']` and the matching `model`. Run `uv run python parse_notebook.py --config …/config.yaml` and `uv run python grade.py --config …/config.yaml` for each (see `research/paper/main.tex` §5.5). Output under `output/` is gitignored unless you force-add.
