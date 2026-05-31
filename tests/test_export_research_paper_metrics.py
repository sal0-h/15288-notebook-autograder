"""Smoke test for scripts/export_research_paper_metrics.py."""

import shutil
import subprocess
import sys
from pathlib import Path


def test_export_research_paper_metrics_runs():
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "export_research_paper_metrics.py"
    paper_dir = root / "research" / "paper"
    snapshot = paper_dir / "metrics_snapshot.json"
    fixture = root / "tests" / "fixtures" / "metrics_snapshot.json"
    out = paper_dir / "human_ai_metrics_wide.csv"

    paper_dir.mkdir(parents=True, exist_ok=True)
    had_snapshot = snapshot.is_file()
    if had_snapshot:
        backup = paper_dir / "metrics_snapshot.json.bak_test"
        shutil.copy2(snapshot, backup)
    shutil.copy2(fixture, snapshot)
    try:
        r = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=False,
        )
        assert r.returncode == 0, r.stderr
        lines = out.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 2, f"Expected header + at least one data row, got: {lines}"
        assert "assignment_name" in lines[0]
        assert "gpt4_mae_total" in lines[0]
    finally:
        if had_snapshot:
            shutil.move(backup, snapshot)
        else:
            snapshot.unlink(missing_ok=True)
