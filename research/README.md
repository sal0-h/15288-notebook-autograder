# Research artifacts

Scratch and large exports stay **gitignored** under `research/*` except the tracked **`paper/`** subtree, which holds draft tables and prose for the human–AI comparison study.

Regenerate tabular outputs after a new compare run:

```bash
python scripts/export_research_paper_metrics.py
```

That script reads `experiment_analysis/<model_tag>/summary.json` (local, usually gitignored) and refreshes `research/paper/human_ai_metrics_wide.csv` when those files exist; otherwise it writes from the last embedded snapshot.
