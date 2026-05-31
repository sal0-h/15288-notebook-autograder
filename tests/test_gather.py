"""Tests for gather.py: submission extraction and metadata parsing."""

import json
from pathlib import Path

import pytest
import yaml

from gather import gather_submissions, get_student_name, load_submission_metadata


class TestGetStudentName:
    def test_ruby_style_submitters(self):
        entry = {":submitters": [{":name": "Alice Smith"}]}
        assert get_student_name(entry) == "Alice Smith"

    def test_empty_submitters(self):
        assert get_student_name({":submitters": []}) is None


class TestLoadSubmissionMetadata:
    def test_load_valid_yml(self, tmp_path):
        meta = {"submission_123": {":submitters": [{":name": "Alice"}]}}
        path = tmp_path / "submission_metadata.yml"
        path.write_text(yaml.dump(meta), encoding="utf-8")
        result = load_submission_metadata(path)
        assert result["submission_123"][":submitters"][0][":name"] == "Alice"


class TestGatherFromFolder:
    def _make_export_folder(
        self, tmp_path, metadata: dict, submissions: list[tuple[str, str]]
    ) -> Path:
        meta_path = tmp_path / "submission_metadata.yml"
        meta_path.write_text(yaml.dump(metadata), encoding="utf-8")
        for folder_name, _nb_content in submissions:
            folder = tmp_path / folder_name
            folder.mkdir()
            nb = {"cells": [], "nbformat": 4, "metadata": {}}
            (folder / "notebook.ipynb").write_text(json.dumps(nb), encoding="utf-8")
        return tmp_path

    def test_missing_metadata_raises(self, tmp_path):
        out = tmp_path / "out"
        with pytest.raises(FileNotFoundError, match="submission_metadata.yml"):
            gather_submissions(tmp_path, out, from_zip=False)

    def test_valid_export_one_student(self, tmp_path):
        meta = {"submission_123": {":submitters": [{":name": "Alice"}]}}
        self._make_export_folder(tmp_path, meta, [("submission_123", "{}")])
        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)
        assert len(result) == 1
        assert result[0]["student_name"] == "Alice"
        assert result[0]["status"] == "ok"

    def test_duplicate_student_names(self, tmp_path):
        meta = {
            "submission_1": {":submitters": [{":name": "Alice"}]},
            "submission_2": {":submitters": [{":name": "Alice"}]},
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
