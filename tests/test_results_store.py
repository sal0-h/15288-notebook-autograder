"""Tests for results_store module."""

import json
import logging
from pathlib import Path

import pytest

from results_store import find_student, load_results, save_results, update_student


def test_load_results_missing_returns_empty(tmp_path):
    path = tmp_path / "graded_results.json"
    assert load_results(path) == []


def test_load_results_empty_file_returns_empty(tmp_path):
    path = tmp_path / "graded_results.json"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupted"):
        load_results(path)


def test_load_results_valid(tmp_path):
    path = tmp_path / "graded_results.json"
    data = [{"student_name": "Alice", "total_score": 5}]
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_results(path) == data


def test_load_results_invalid_json_raises(tmp_path):
    path = tmp_path / "graded_results.json"
    path.write_text("{ invalid }", encoding="utf-8")
    with pytest.raises(ValueError, match="corrupted"):
        load_results(path)


def test_save_results_deduplicates_by_student_name(tmp_path):
    """save_results deduplicates by student_name, keeping last occurrence."""
    path = tmp_path / "graded_results.json"
    data = [
        {"student_name": "Alice", "total_score": 5},
        {"student_name": "Bob", "total_score": 3},
        {"student_name": "Alice", "total_score": 7},
    ]
    save_results(path, data)
    loaded = load_results(path)
    assert len(loaded) == 2
    alice = next(r for r in loaded if r["student_name"] == "Alice")
    assert alice["total_score"] == 7


def test_save_results_creates_dir(tmp_path):
    path = tmp_path / "output" / "graded_results.json"
    save_results(path, [])
    assert path.exists()
    assert load_results(path) == []


def test_update_student_append():
    results = []
    update_student(results, "Alice", {"student_name": "Alice", "score": 5})
    assert results == [{"student_name": "Alice", "score": 5}]


def test_update_student_update():
    results = [
        {"student_name": "Alice", "score": 1},
        {"student_name": "Bob", "score": 2},
    ]
    update_student(results, "Alice", {"student_name": "Alice", "score": 10})
    assert results[0]["score"] == 10
    assert results[1]["score"] == 2


def test_find_student():
    results = [
        {"student_name": "Alice", "score": 1},
        {"student_name": "Bob", "score": 2},
    ]
    assert find_student(results, "Alice") == {"student_name": "Alice", "score": 1}
    assert find_student(results, "Bob") == {"student_name": "Bob", "score": 2}
    assert find_student(results, "Carol") is None


def test_roundtrip(tmp_path):
    path = tmp_path / "graded_results.json"
    data = [
        {"student_name": "Alice", "questions": {"1.1": {"score": 5}}},
        {"student_name": "Bob", "questions": {"1.1": {"score": 3}}},
    ]
    save_results(path, data)
    loaded = load_results(path)
    assert loaded == data
    update_student(
        loaded, "Alice", {"student_name": "Alice", "questions": {"1.1": {"score": 4}}}
    )
    save_results(path, loaded)
    assert load_results(path)[0]["questions"]["1.1"]["score"] == 4


def test_save_results_logs_with_provided_logger(tmp_path, caplog):
    path = tmp_path / "graded_results.json"
    custom_logger = logging.getLogger("tests.results_store")

    with caplog.at_level(logging.INFO, logger="tests.results_store"):
        save_results(
            path, [{"student_name": "Alice", "score": 5}], logger_obj=custom_logger
        )

    assert "Saved 1 graded result entries" in caplog.text


def test_update_student_logs_with_provided_logger(caplog):
    custom_logger = logging.getLogger("tests.results_store")
    results = []

    with caplog.at_level(logging.INFO, logger="tests.results_store"):
        update_student(
            results,
            "Alice",
            {"student_name": "Alice", "score": 5},
            logger_obj=custom_logger,
        )

    assert "Added new graded result for Alice" in caplog.text
