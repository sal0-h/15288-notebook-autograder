"""Tests for Gradescope submitter keys, manifest building, and runtime resolution."""

import json

import pytest

from export import build_precomputed_manifest, _stem_to_submitter_key_map
from gradescope_submitters import (
    canonical_submitter_key_from_runtime_users,
    submitter_key_from_results_filename,
    submitter_key_from_yaml_submitters,
    submitter_key_to_results_filename,
)
from gradescope_runtime import (
    _assignment_invariant_ok,
    resolve_precomputed_path,
)


class TestCanonicalSubmitterKey:
    def test_single_user(self):
        assert (
            canonical_submitter_key_from_runtime_users([{"id": 42, "name": "A"}])
            == "42"
        )

    def test_group_sorted_order(self):
        assert (
            canonical_submitter_key_from_runtime_users(
                [
                    {"id": 9, "name": "B"},
                    {"id": 2, "name": "A"},
                ]
            )
            == "2,9"
        )

    def test_group_reverse_input_same_key(self):
        k1 = canonical_submitter_key_from_runtime_users(
            [{"id": 3, "name": "X"}, {"id": 1, "name": "Y"}]
        )
        k2 = canonical_submitter_key_from_runtime_users(
            [{"id": 1, "name": "Y"}, {"id": 3, "name": "X"}]
        )
        assert k1 == k2 == "1,3"

    def test_missing_id_returns_none(self):
        assert canonical_submitter_key_from_runtime_users([{"name": "A"}]) is None

    def test_empty_users(self):
        assert canonical_submitter_key_from_runtime_users([]) is None


class TestYamlSubmitters:
    def test_yaml_ids_sorted(self):
        assert (
            submitter_key_from_yaml_submitters(
                [{":id": 5, ":name": "A"}, {":id": 1, ":name": "B"}]
            )
            == "1,5"
        )

    def test_missing_id(self):
        assert submitter_key_from_yaml_submitters([{":name": "A"}]) is None


class TestFilenameHelpers:
    def test_comma_to_underscore(self):
        assert submitter_key_to_results_filename("1,2,3") == "1__2__3"
        assert submitter_key_from_results_filename("1__2__3") == "1,2,3"


class TestStemToSubmitterKeyMap:
    def test_ambiguous_stem_raises(self):
        with pytest.raises(ValueError, match="multiple keys"):
            _stem_to_submitter_key_map({"1": "Alice", "2": "Alice"})


class TestBuildPrecomputedManifest:
    def test_builds_entries(self):
        m = build_precomputed_manifest(
            {"999": "Alice"},
            ["Alice"],
            assignment_id=10,
            course_id=20,
        )
        assert m is not None
        assert m["schema_version"] == 1
        assert m["assignment_id"] == 10
        assert m["course_id"] == 20
        assert m["entries"]["999"]["file"] == "results/999.json"
        assert m["entries"]["999"]["display_name"] == "Alice"

    def test_empty_stem_map(self):
        assert (
            build_precomputed_manifest({}, ["Alice"], assignment_id=1, course_id=2)
            is None
        )


class TestRuntimeResolve:
    @pytest.fixture
    def source(self, tmp_path, monkeypatch):
        import gradescope_runtime as gr

        root = tmp_path / "source"
        (root / "results").mkdir(parents=True)
        monkeypatch.setattr(gr, "SOURCE_DIR", root)
        monkeypatch.setattr(gr, "PRECOMPUTED_DIR", root / "results")
        return root

    def test_id_keyed_file(self, source, monkeypatch):
        import gradescope_runtime as gr

        monkeypatch.setattr(gr, "LEGACY_MAP_PATH", source / "student_name_map.json")
        payload = {"tests": [{"name": "t", "score": 1, "max_score": 1}]}
        (source / "results" / "42.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        manifest = {
            "schema_version": 1,
            "assignment_id": None,
            "course_id": None,
            "entries": {"42": {"file": "results/42.json", "display_name": "Alice"}},
        }
        meta = {"users": [{"id": 42, "name": "Alice"}], "assignment": {}}
        p = resolve_precomputed_path(meta, manifest)
        assert p is not None
        assert json.loads(p.read_text(encoding="utf-8")) == payload

    def test_legacy_name_fallback(self, source, monkeypatch):
        import gradescope_runtime as gr

        monkeypatch.setattr(gr, "LEGACY_MAP_PATH", source / "student_name_map.json")
        (source / "student_name_map.json").write_text(
            json.dumps({"alice": "Alice"}), encoding="utf-8"
        )
        payload = {"tests": []}
        (source / "results" / "Alice.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        meta = {"users": [{"id": 99, "name": "Alice"}], "assignment": {}}
        p = resolve_precomputed_path(meta, None)
        assert p is not None and json.loads(p.read_text(encoding="utf-8")) == payload

    def test_assignment_mismatch_returns_none(self, source, monkeypatch):
        import gradescope_runtime as gr

        monkeypatch.setattr(gr, "LEGACY_MAP_PATH", source / "student_name_map.json")
        manifest = {
            "schema_version": 1,
            "assignment_id": 1,
            "course_id": 100,
            "entries": {},
        }
        meta = {
            "users": [{"id": 1, "name": "A"}],
            "assignment": {"id": 999, "course_id": 100},
        }
        assert resolve_precomputed_path(meta, manifest) is None


class TestAssignmentInvariant:
    def test_skips_when_manifest_unset(self):
        ok, _ = _assignment_invariant_ok(
            {"assignment_id": None, "course_id": None},
            {"assignment": {"id": 1, "course_id": 2}},
        )
        assert ok

    def test_detects_assignment_id_mismatch(self):
        ok, msg = _assignment_invariant_ok(
            {"assignment_id": 1, "course_id": None},
            {"assignment": {"id": 2, "course_id": 99}},
        )
        assert not ok
        assert "assignment_id" in msg
