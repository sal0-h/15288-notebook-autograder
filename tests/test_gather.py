"""Tests for gather.py: ZIP extraction, metadata parsing, filename sanitization."""

import json
from pathlib import Path

import pytest
import yaml

from gather import (
    gather_submissions,
    get_student_name,
    load_submission_metadata,
)

# ---------------------------------------------------------------------------
# get_student_name (Gradescope export uses Ruby-style YAML keys)
# ---------------------------------------------------------------------------


class TestGetStudentName:
    def test_ruby_style_submitters(self):
        entry = {":submitters": [{":name": "Alice Smith"}]}
        assert get_student_name(entry) == "Alice Smith"

    def test_empty_submitters(self):
        assert get_student_name({":submitters": []}) is None
        assert get_student_name({}) is None

    def test_no_name_key(self):
        entry = {":submitters": [{":id": 123}]}
        assert get_student_name(entry) is None


# ---------------------------------------------------------------------------
# load_submission_metadata
# ---------------------------------------------------------------------------


class TestLoadSubmissionMetadata:
    def test_load_valid_yml(self, tmp_path):
        meta = {
            "submission_123": {":submitters": [{":name": "Alice"}]},
        }
        path = tmp_path / "submission_metadata.yml"
        path.write_text(yaml.dump(meta), encoding="utf-8")
        result = load_submission_metadata(path)
        assert "submission_123" in result
        assert result["submission_123"][":submitters"][0][":name"] == "Alice"

    def test_empty_file(self, tmp_path):
        path = tmp_path / "submission_metadata.yml"
        path.write_text("", encoding="utf-8")
        assert load_submission_metadata(path) is None


# ---------------------------------------------------------------------------
# gather_submissions (from folder)
# ---------------------------------------------------------------------------


class TestGatherFromFolder:
    def _make_export_folder(
        self, tmp_path, metadata: dict, submissions: list[tuple[str, str]]
    ) -> Path:
        """Create a Gradescope-style export folder. submissions: [(folder_name, notebook_content)]"""
        meta_path = tmp_path / "submission_metadata.yml"
        meta_path.write_text(yaml.dump(metadata), encoding="utf-8")
        for folder_name, nb_content in submissions:
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
        assert result[0]["filename"].endswith(".ipynb")
        assert (out / result[0]["filename"]).exists()

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

    def test_filename_sanitization(self, tmp_path):
        meta = {
            "submission_1": {":submitters": [{":name": "Alice/Test:Student"}]},
        }
        self._make_export_folder(tmp_path, meta, [("submission_1", "{}")])
        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)
        assert result[0]["status"] == "ok"
        assert "/" not in result[0]["filename"]
        assert ":" not in result[0]["filename"]

    def test_missing_folder(self, tmp_path):
        meta = {"submission_999": {":submitters": [{":name": "Bob"}]}}
        meta_path = tmp_path / "submission_metadata.yml"
        meta_path.write_text(yaml.dump(meta), encoding="utf-8")
        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)
        assert len(result) == 1
        assert result[0]["status"] == "missing"

    def test_persists_email_stem_map(self, tmp_path):
        """gather_submissions creates email_stem_map.json for autograder lookup."""
        meta = {
            "submission_1": {
                ":submitters": [
                    {":name": "Alice Johnson", ":email": "alice@example.com"}
                ]
            }
        }
        (tmp_path / "submission_metadata.yml").write_text(
            yaml.dump(meta), encoding="utf-8"
        )
        folder = tmp_path / "submission_1"
        folder.mkdir()
        nb = {"cells": [], "nbformat": 4, "metadata": {}}
        (folder / "Alice Johnson_hw1_att2.ipynb").write_text(
            json.dumps(nb), encoding="utf-8"
        )

        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)

        assert len(result) == 1
        assert result[0]["status"] == "ok"

        map_path = out.parent / "email_stem_map.json"
        assert map_path.exists()
        map_data = json.loads(map_path.read_text(encoding="utf-8"))
        assert map_data["alice@example.com"] == "Alice Johnson"

    def test_duplicate_sanitized_name_overwrites(self, tmp_path):
        """When sanitized names collide, the last submission wins (overwrite)."""
        meta = {
            "submission_1": {":submitters": [{":name": "A/B"}]},
            "submission_2": {":submitters": [{":name": "AB"}]},
        }
        (tmp_path / "submission_metadata.yml").write_text(
            yaml.dump(meta), encoding="utf-8"
        )
        nb1 = {
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["first"],
                    "metadata": {},
                    "outputs": [],
                }
            ],
            "nbformat": 4,
            "metadata": {},
        }
        nb2 = {
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["second"],
                    "metadata": {},
                    "outputs": [],
                }
            ],
            "nbformat": 4,
            "metadata": {},
        }
        folder1 = tmp_path / "submission_1"
        folder1.mkdir()
        (folder1 / "notebook.ipynb").write_text(json.dumps(nb1), encoding="utf-8")
        folder2 = tmp_path / "submission_2"
        folder2.mkdir()
        (folder2 / "notebook.ipynb").write_text(json.dumps(nb2), encoding="utf-8")

        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)

        # Both sanitize to AB.ipynb — second overwrites first
        assert (out / "AB.ipynb").exists()
        content = json.loads((out / "AB.ipynb").read_text(encoding="utf-8"))
        assert content["cells"][0]["source"] == ["second"]
