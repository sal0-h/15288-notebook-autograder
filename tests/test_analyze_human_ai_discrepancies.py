"""Tests for scripts/analyze_human_ai_discrepancies.py."""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path


def _import_mod():
    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from scripts import analyze_human_ai_discrepancies as mod

    return mod


def _graded_entry(
    stem: str,
    *,
    total: float,
    q1_score: float,
    q1_max: float,
) -> dict:
    return {
        "student_name": stem,
        "total_score": total,
        "total_max": 100.0,
        "questions": {
            "1.1": {
                "score": q1_score,
                "max": q1_max,
                "feedback": f"fb-{stem}",
                "confidence": "high",
                "requires_review": False,
            }
        },
    }


def test_task2_task3_row_counts(tmp_path, monkeypatch):
    mod = _import_mod()
    human = tmp_path / "human.csv"
    human.write_text(
        "anon_id,Total Score,Max Points,1.1\n"
        "a,10,100,0\n"  # task2: human 0, AI high
        "b,10,100,8\n"  # task3: human 8 > 0.7*10, AI 0
        "c,10,100,5\n",  # neither
        encoding="utf-8",
    )
    graded = tmp_path / "graded.json"
    graded.write_text(
        json.dumps(
            [
                _graded_entry("a", total=50.0, q1_score=8.0, q1_max=10.0),
                _graded_entry("b", total=2.0, q1_score=0.0, q1_max=10.0),
                _graded_entry("c", total=10.0, q1_score=5.0, q1_max=10.0),
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        mod,
        "human_csv_path",
        lambda assignment_name, *, project_root: human,
    )
    monkeypatch.setattr(
        mod,
        "graded_results_path",
        lambda assignment_name, *, project_root, model_tag, use_live: graded,
    )

    md = mod.build_markdown_report(
        project_root=tmp_path,
        labs=["S25_LabTest_2"],
        model_tag="gpt-4.1",
        use_live=False,
        over_frac=0.7,
        under_frac=0.7,
    )
    assert "| S25_LabTest_2 | a | 1.1 | 0.0 | 8.0 | 10.0 |" in md
    assert "| S25_LabTest_2 | b | 1.1 | 8.0 | 0.0 | 10.0 |" in md
    assert md.count("| S25_LabTest_2 | c |") == 0


def test_task4_quartile_stats_invariant_to_row_order(tmp_path, monkeypatch):
    mod = _import_mod()
    graded = tmp_path / "graded.json"
    stems = [f"{i:03d}" for i in range(1, 9)]
    rows = []
    for i, stem in enumerate(stems, start=1):
        rows.append(_graded_entry(stem, total=float(i), q1_score=1.0, q1_max=10.0))
    graded.write_text(json.dumps(rows), encoding="utf-8")

    def run_with_human_csv(order: list[str]) -> str:
        lines = ["anon_id,Total Score,Max Points,1.1"]
        for stem in order:
            h = int(stem)
            lines.append(f"{stem},{h},100,1")
        human = tmp_path / f"human_{'_'.join(order)}.csv"
        human.write_text("\n".join(lines) + "\n", encoding="utf-8")
        monkeypatch.setattr(
            mod,
            "human_csv_path",
            lambda assignment_name, *, project_root: human,
        )
        monkeypatch.setattr(
            mod,
            "graded_results_path",
            lambda assignment_name, *, project_root, model_tag, use_live: graded,
        )
        return mod.build_markdown_report(
            project_root=tmp_path,
            labs=["S25_LabTest_2"],
            model_tag="x",
            use_live=False,
            over_frac=0.7,
            under_frac=0.7,
        )

    order_a = stems.copy()
    order_b = stems.copy()
    random.seed(42)
    random.shuffle(order_b)
    m1 = run_with_human_csv(order_a)
    m2 = run_with_human_csv(order_b)
    t4_1 = m1.split("## Task 4", 1)[1].split("## Task 5", 1)[0]
    t4_2 = m2.split("## Task 4", 1)[1].split("## Task 5", 1)[0]
    assert t4_1 == t4_2
    assert "| Bottom 25% (by human total) | 2 |" in m1
    assert "| Middle 50% | 4 |" in m1
    assert "| Top 25% (by human total) | 2 |" in m1


def test_detect_grader_like_columns_skips_autograder():
    from scripts._human_ai_join import detect_grader_like_columns

    assert detect_grader_like_columns(["1: Autograder (1.0 pts)"]) == []
    assert detect_grader_like_columns(["Graded by TA", "1: Autograder (1.0 pts)"]) == [
        "Graded by TA"
    ]


def test_task1_detects_grader_columns_in_raw(tmp_path):
    mod = _import_mod()
    raw = tmp_path / "experiment_data" / "X" / "Lab" / "_raw" / "export.csv"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text("anon_id,Grader Notes,Score\n1,x,0\n", encoding="utf-8")
    text = mod._task1_section(tmp_path)
    assert "Grader Notes" in text
    assert "export.csv" in text
