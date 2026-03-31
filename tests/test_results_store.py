"""Tests for results_store module."""

import json
import logging
from pathlib import Path

from results_models import GradedResult, Question
from results_store import find_student, load_results, save_results, update_student


def _gr(name: str, score: float = 0.0, **questions) -> GradedResult:
    qs = {qid: Question(score=s, max=5.0, feedback="") for qid, s in questions.items()}
    return GradedResult(student_name=name, total_score=score, questions=qs)


def test_load_results_missing_returns_empty(tmp_path):
    path = tmp_path / "graded_results.json"
    assert load_results(path) == []


def test_load_results_empty_file_returns_empty(tmp_path):
    path = tmp_path / "graded_results.json"
    path.write_text("", encoding="utf-8")
    assert load_results(path) == []
    assert (tmp_path / "graded_results.json.broken").exists()


def test_load_results_valid(tmp_path):
    path = tmp_path / "graded_results.json"
    path.write_text(
        json.dumps([{"student_name": "Alice", "total_score": 5.0}]), encoding="utf-8"
    )
    loaded = load_results(path)
    assert len(loaded) == 1
    assert loaded[0].student_name == "Alice"
    assert loaded[0].total_score == 5.0


def test_load_results_invalid_json_recover(tmp_path):
    path = tmp_path / "graded_results.json"
    path.write_text("{ invalid }", encoding="utf-8")
    assert load_results(path) == []
    assert (tmp_path / "graded_results.json.broken").exists()


def test_save_results_deduplicates_by_student_name(tmp_path):
    """save_results deduplicates by student_name, keeping last occurrence."""
    path = tmp_path / "graded_results.json"
    data = [
        _gr("Alice", 5.0),
        _gr("Bob", 3.0),
        _gr("Alice", 7.0),
    ]
    save_results(path, data)
    loaded = load_results(path)
    assert len(loaded) == 2
    alice = next(r for r in loaded if r.student_name == "Alice")
    assert alice.total_score == 7.0


def test_save_results_creates_dir(tmp_path):
    path = tmp_path / "output" / "graded_results.json"
    save_results(path, [])
    assert path.exists()
    assert load_results(path) == []


def test_update_student_append():
    results = []
    update_student(results, "Alice", _gr("Alice", 5.0))
    assert len(results) == 1
    assert results[0].student_name == "Alice"
    assert results[0].total_score == 5.0


def test_update_student_update():
    results = [_gr("Alice", 1.0), _gr("Bob", 2.0)]
    update_student(results, "Alice", _gr("Alice", 10.0))
    assert results[0].total_score == 10.0
    assert find_student(results, "Bob").total_score == 2.0


def test_find_student():
    results = [_gr("Alice", 1.0), _gr("Bob", 2.0)]
    assert find_student(results, "Alice").total_score == 1.0
    assert find_student(results, "Bob").total_score == 2.0
    assert find_student(results, "Carol") is None


def test_roundtrip(tmp_path):
    path = tmp_path / "graded_results.json"
    data = [
        _gr("Alice", **{"1.1": 5.0}),
        _gr("Bob", **{"1.1": 3.0}),
    ]
    save_results(path, data)
    loaded = load_results(path)
    assert len(loaded) == 2
    assert loaded[0].questions["1.1"].score == 5.0
    update_student(loaded, "Alice", _gr("Alice", **{"1.1": 4.0}))
    save_results(path, loaded)
    assert load_results(path)[0].questions["1.1"].score == 4.0


def test_save_results_logs_with_provided_logger(tmp_path, caplog):
    path = tmp_path / "graded_results.json"
    custom_logger = logging.getLogger("tests.results_store")

    with caplog.at_level(logging.INFO, logger="tests.results_store"):
        save_results(path, [_gr("Alice", 5.0)], logger_obj=custom_logger)

    assert "Saved 1 graded result entries" in caplog.text


def test_update_student_logs_with_provided_logger(caplog):
    custom_logger = logging.getLogger("tests.results_store")
    results = []

    with caplog.at_level(logging.INFO, logger="tests.results_store"):
        update_student(results, "Alice", _gr("Alice", 5.0), logger_obj=custom_logger)

    assert "Added new graded result for Alice" in caplog.text
