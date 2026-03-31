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
from gradescope_submitters import submitter_key_from_yaml_submitters

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

    def test_persists_deterministic_name_map(self, tmp_path):
        """gather_submissions creates student_name_map.json for export-time lookup."""
        meta = {"submission_1": {":submitters": [{":name": "Alice Johnson"}]}}
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
        assert result[0]["filename"] == "Alice Johnson.ipynb"
        assert (out / "Alice Johnson.ipynb").exists()

        # Verify map was persisted
        map_path = out.parent / "student_name_map.json"
        assert map_path.exists()
        map_data = json.loads(map_path.read_text(encoding="utf-8"))
        # Normalized name should map to the output stem (without .ipynb)
        assert map_data["alicejohnson"] == "Alice Johnson"
        stem_map_path = out.parent / "submitter_stem_map.json"
        assert stem_map_path.exists()
        assert json.loads(stem_map_path.read_text(encoding="utf-8")) == {}

    def test_map_handles_collision_suffixes(self, tmp_path):
        """When sanitized names collide, map entries use the collision-suffixed stems."""
        meta = {
            "submission_1": {":submitters": [{":name": "A/B"}]},
            "submission_2": {":submitters": [{":name": "AB"}]},
        }
        (tmp_path / "submission_metadata.yml").write_text(
            yaml.dump(meta), encoding="utf-8"
        )
        nb = {"cells": [], "nbformat": 4, "metadata": {}}
        for key in ("submission_1", "submission_2"):
            folder = tmp_path / key
            folder.mkdir()
            (folder / "notebook.ipynb").write_text(json.dumps(nb), encoding="utf-8")

        out = tmp_path / "out"
        result = gather_submissions(tmp_path, out, from_zip=False)

        names = sorted(r["filename"] for r in result if r["status"] == "ok")
        assert names == ["AB.ipynb", "AB_2.ipynb"]
        assert (out / "AB.ipynb").exists()
        assert (out / "AB_2.ipynb").exists()

        # Verify both map entries point to the correct stems
        map_path = out.parent / "student_name_map.json"
        map_data = json.loads(map_path.read_text(encoding="utf-8"))
        # Both normalize to "ab" (alphanumeric only)
        assert "ab" in map_data
        assert map_data["ab"] in ["AB", "AB_2"]

    def test_submitter_stem_map_with_gradescope_ids(self, tmp_path):
        meta = {
            "submission_1": {
                ":submitters": [{":name": "Alice", ":id": 4242}],
            },
        }
        (tmp_path / "submission_metadata.yml").write_text(
            yaml.dump(meta), encoding="utf-8"
        )
        folder = tmp_path / "submission_1"
        folder.mkdir()
        nb = {"cells": [], "nbformat": 4, "metadata": {}}
        (folder / "nb.ipynb").write_text(json.dumps(nb), encoding="utf-8")
        out = tmp_path / "out"
        gather_submissions(tmp_path, out, from_zip=False)
        stem_map = json.loads(
            (out.parent / "submitter_stem_map.json").read_text(encoding="utf-8")
        )
        assert stem_map["4242"] == "Alice"
        assert (
            submitter_key_from_yaml_submitters(meta["submission_1"][":submitters"])
            == "4242"
        )
