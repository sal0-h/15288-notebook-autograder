"""Tests for results_store module."""

import json
from pathlib import Path

from results_models import GradedResult, Question
from results_store import load_results, save_results, update_student


def _gr(name: str, score: float = 0.0, **questions) -> GradedResult:
    qs = {qid: Question(score=s, max=5.0, feedback="") for qid, s in questions.items()}
    return GradedResult(student_name=name, total_score=score, questions=qs)


def test_load_results_missing_returns_empty(tmp_path):
    path = tmp_path / "graded_results.json"
    assert load_results(path) == []


def test_load_results_invalid_json_recover(tmp_path):
    path = tmp_path / "graded_results.json"
    path.write_text("{ invalid }", encoding="utf-8")
    assert load_results(path) == []
    assert list(tmp_path.glob("graded_results.json.broken*"))


def test_save_results_deduplicates_by_student_name(tmp_path):
    path = tmp_path / "graded_results.json"
    data = [_gr("Alice", 5.0), _gr("Bob", 3.0), _gr("Alice", 7.0)]
    save_results(path, data)
    loaded = load_results(path)
    assert len(loaded) == 2
    alice = next(r for r in loaded if r.student_name == "Alice")
    assert alice.total_score == 7.0


def test_update_student_append_and_update():
    results = []
    update_student(results, "Alice", _gr("Alice", 5.0))
    assert len(results) == 1
    update_student(results, "Alice", _gr("Alice", 10.0))
    assert results[0].total_score == 10.0


def test_roundtrip(tmp_path):
    path = tmp_path / "graded_results.json"
    data = [_gr("Alice", **{"1.1": 5.0}), _gr("Bob", **{"1.1": 3.0})]
    save_results(path, data)
    loaded = load_results(path)
    assert len(loaded) == 2
    update_student(loaded, "Alice", _gr("Alice", **{"1.1": 4.0}))
    save_results(path, loaded)
    assert load_results(path)[0].questions["1.1"].score == 4.0
