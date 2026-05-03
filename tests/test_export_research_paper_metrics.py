"""Smoke test for scripts/export_research_paper_metrics.py."""

import subprocess
import sys
from pathlib import Path


def test_export_research_paper_metrics_runs():
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "export_research_paper_metrics.py"
    out = root / "research" / "paper" / "human_ai_metrics_wide.csv"
    r = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    # At least one data row must be present (header + ≥1 lab).
    # The exact count depends on which experiment_data/ labs are staged locally
    # (that directory is gitignored), so we only assert structure, not row count.
    assert len(lines) >= 2, f"Expected header + at least one data row, got: {lines}"
    # Header must contain the expected columns
    assert "assignment_name" in lines[0]
    assert "gpt4_mae_total" in lines[0]
