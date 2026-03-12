"""Tests for gather.py: ZIP extraction, metadata parsing, filename sanitization."""

import json
from pathlib import Path

import pytest

from gather import (
    gather_submissions,
    get_student_name,
    load_submission_metadata,
)

# ---------------------------------------------------------------------------
# get_student_name
# ---------------------------------------------------------------------------


class TestGetStudentName:
    def test_plain_submitters(self):
        entry = {"submitters": [{"name": "Alice Smith"}]}
        assert get_student_name(entry) == "Alice Smith"

    def test_ruby_style_keys(self):
        entry = {":submitters": [{":name": "Bob Jones"}]}
        assert get_student_name(entry) == "Bob Jones"

    def test_empty_submitters(self):
        assert get_student_name({"submitters": []}) is None
        assert get_student_name({}) is None

    def test_no_name(self):
        entry = {"submitters": [{"id": 123}]}
        assert get_student_name(entry) is None


# ---------------------------------------------------------------------------
# load_submission_metadata
# ---------------------------------------------------------------------------


class TestLoadSubmissionMetadata:
    def test_load_valid_yml(self, tmp_path):
        meta = {"submission_123": {"submitters": [{"name": "Alice"}]}}
        import yaml

        path = tmp_path / "submission_metadata.yml"
        path.write_text(yaml.dump(meta), encoding="utf-8")
        result = load_submission_metadata(path)
        assert "submission_123" in result
        assert result["submission_123"]["submitters"][0]["name"] == "Alice"

    def test_empty_file(self, tmp_path):
        path = tmp_path / "submission_metadata.yml"
        path.write_text("", encoding="utf-8")
        result = load_submission_metadata(path)
        assert result == {}


# ---------------------------------------------------------------------------
# gather_submissions (from folder)
# ---------------------------------------------------------------------------


class TestGatherFromFolder:
    def _make_export_folder(
        self, tmp_path, metadata: dict, submissions: list[tuple[str, str]]
    ) -> Path:
        """Create a Gradescope-style export folder. submissions: [(folder_name, notebook_content)]"""
        import yaml

        meta_path = tmp_path / "submission_metadata.yml"
        meta_path.write_text(yaml.dump(metadata), encoding="utf-8")
        for folder_name, nb_content in submissions:
            folder = tmp_path / folder_name
            folder.mkdir()
            nb = {"cells": [], "nbformat": 4, "metadata": {}}
            (folder / "notebook.ipynb").write_text(json.dumps(nb), encoding="utf-8")
        return tmp_path

    def test_missing_metadata_returns_empty(self, tmp_path):
        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)
        assert result == []

    def test_valid_export_one_student(self, tmp_path):
        meta = {"submission_123": {"submitters": [{"name": "Alice"}]}}
        self._make_export_folder(tmp_path, meta, [("submission_123", "{}")])
        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)
        assert len(result) == 1
        assert result[0]["student_name"] == "Alice"
        assert result[0]["status"] == "ok"
        assert result[0]["filename"].endswith(".ipynb")
        assert (out / result[0]["filename"]).exists()

    def test_duplicate_student_names(self, tmp_path):
        meta = {
            "submission_1": {"submitters": [{"name": "Alice"}]},
            "submission_2": {"submitters": [{"name": "Alice"}]},
        }
        self._make_export_folder(
            tmp_path, meta, [("submission_1", "{}"), ("submission_2", "{}")]
        )
        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)
        ok = [r for r in result if r["status"] == "ok"]
        dup = [r for r in result if r["status"] == "duplicate"]
        assert len(ok) == 1
        assert len(dup) == 1

    def test_filename_sanitization(self, tmp_path):
        meta = {"submission_1": {"submitters": [{"name": "Alice/Test:Student"}]}}
        self._make_export_folder(tmp_path, meta, [("submission_1", "{}")])
        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)
        assert result[0]["status"] == "ok"
        assert "/" not in result[0]["filename"]
        assert ":" not in result[0]["filename"]

    def test_missing_folder(self, tmp_path):
        import yaml

        meta = {"submission_999": {"submitters": [{"name": "Bob"}]}}
        meta_path = tmp_path / "submission_metadata.yml"
        meta_path.write_text(yaml.dump(meta), encoding="utf-8")
        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)
        assert len(result) == 1
        assert result[0]["status"] == "missing"
