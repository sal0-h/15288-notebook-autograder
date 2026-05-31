"""Smoke test for scripts/report_human_ai_question_gaps.py."""

import json
import subprocess
import sys
from pathlib import Path


def _write_gap_fixtures(root: Path) -> tuple[Path, Path]:
    """Minimal human + AI rows so the report lists at least one student line."""
    human_dir = root / "experiment_data" / "S26" / "LabTest_2"
    human_dir.mkdir(parents=True, exist_ok=True)
    human_csv = human_dir / "human_grades.csv"
    human_csv.write_text(
        "anon_id,Total Score,Max Points,1.1\n001,3,5,3\n",
        encoding="utf-8",
    )

    graded_dir = root / "output" / "S26_LabTest_2" / "experiment_runs" / "gpt-4.1"
    graded_dir.mkdir(parents=True, exist_ok=True)
    graded_path = graded_dir / "graded_results.json"
    graded_path.write_text(
        json.dumps(
            [
                {
                    "student_name": "001",
                    "questions": {
                        "1.1": {
                            "score": 5.0,
                            "max": 5.0,
                            "feedback": "Full credit.",
                            "confidence": "high",
                            "requires_review": False,
                        }
                    },
                    "total_score": 5.0,
                    "total_max": 5.0,
                }
            ]
        ),
        encoding="utf-8",
    )
    return human_csv, graded_path


def test_report_human_ai_question_gaps_writes_file(tmp_path):
    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "report_human_ai_question_gaps.py"
    out = tmp_path / "rep.md"

    human_csv, graded_path = _write_gap_fixtures(root)
    try:
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
        assert "student 001" in text
    finally:
        human_csv.unlink(missing_ok=True)
        graded_path.unlink(missing_ok=True)
        for d in (
            human_csv.parent,
            graded_path.parent,
            graded_path.parent.parent,
            graded_path.parent.parent.parent,
        ):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
