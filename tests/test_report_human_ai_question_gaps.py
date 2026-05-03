"""Smoke test for scripts/report_human_ai_question_gaps.py."""

import subprocess
import sys
from pathlib import Path


def test_report_human_ai_question_gaps_writes_file(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "report_human_ai_question_gaps.py"
    out = tmp_path / "rep.md"
    r = subprocess.run(
        [
            sys.executable,
            str(script),
            "--model-tag",
            "gpt-4.1",
            "--labs",
            "S26_LabTest_2",
            "--top",
            "3",
            "--out",
            str(out),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr + r.stdout
    text = out.read_text(encoding="utf-8")
    assert "S26_LabTest_2" in text
    assert "student" in text
