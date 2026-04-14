"""Gradescope autograder harness: copy precomputed ``results.json`` from bundled artifacts.

Invoked from ``run_autograder`` on Gradescope (working directory ``/autograder``).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from gradescope_submitters import (
    MANIFEST_SCHEMA_VERSION,
    canonical_submitter_key_from_runtime_users,
    submitter_key_to_results_filename,
)

RESULTS_DIR = Path("/autograder/results")
SOURCE_DIR = Path("/autograder/source")
PRECOMPUTED_DIR = SOURCE_DIR / "results"
METADATA_PATH = Path("/autograder/submission_metadata.json")
MANIFEST_PATH = SOURCE_DIR / "precomputed_manifest.json"
LEGACY_MAP_PATH = SOURCE_DIR / "student_name_map.json"
OUT_PATH = RESULTS_DIR / "results.json"


def error_response(message: str) -> dict:
    return {
        "output": message,
        "tests": [
            {
                "name": "Autograder Status",
                "score": 0,
                "max_score": 0,
                "output": message,
                "visibility": "visible",
            }
        ],
    }


def _normalize_name(name: str) -> str:
    return "".join(c for c in name.strip().lower() if c.isalnum())


def _assignment_invariant_ok(manifest: dict, meta: dict) -> tuple[bool, str]:
    """Return (ok, stderr diagnostic). Skip check when manifest omits ids."""
    want_aid = manifest.get("assignment_id")
    want_cid = manifest.get("course_id")
    if want_aid is None and want_cid is None:
        return True, ""
    assignment = meta.get("assignment") or {}
    got_aid = assignment.get("id")
    got_cid = assignment.get("course_id")
    if want_aid is not None and got_aid is not None:
        if int(want_aid) != int(got_aid):
            return (
                False,
                f"precomputed_manifest assignment_id={want_aid} != submission assignment.id={got_aid}",
            )
    if want_cid is not None and got_cid is not None:
        if int(want_cid) != int(got_cid):
            return (
                False,
                f"precomputed_manifest course_id={want_cid} != submission course_id={got_cid}",
            )
    return True, ""


def resolve_precomputed_path(meta: dict, manifest: dict | None) -> Path | None:
    """Return path to precomputed JSON under ``PRECOMPUTED_DIR``, or None."""
    users = meta.get("users") or []

    if manifest and int(manifest.get("schema_version", 0)) == MANIFEST_SCHEMA_VERSION:
        ok, diag = _assignment_invariant_ok(manifest, meta)
        if not ok:
            print(diag, file=sys.stderr)
            return None
        key = canonical_submitter_key_from_runtime_users(
            users if isinstance(users, list) else []
        )
        if not key:
            print(
                "manifest present but could not build submitter key from users (missing id?)",
                file=sys.stderr,
            )
        else:
            entries = manifest.get("entries") or {}
            ent = entries.get(key)
            if isinstance(ent, dict):
                rel = ent.get("file")
                if isinstance(rel, str):
                    candidate = SOURCE_DIR / rel
                    if candidate.is_file():
                        return candidate
            fname = f"{submitter_key_to_results_filename(key)}.json"
            id_keyed = PRECOMPUTED_DIR / fname
            if id_keyed.is_file():
                return id_keyed

    # Legacy name-based lookup (Phase 1 fallback; missing :id in export YAML)
    if not LEGACY_MAP_PATH.is_file():
        return None
    with open(LEGACY_MAP_PATH, encoding="utf-8") as f:
        student_name_map = json.load(f)
    student_name = (users[0].get("name", "") or "").strip() if users else ""
    if not student_name or not PRECOMPUTED_DIR.is_dir():
        return None
    normalized_student = _normalize_name(student_name)
    stem = student_name_map.get(normalized_student)
    if not stem:
        return None
    legacy_path = PRECOMPUTED_DIR / f"{stem}.json"
    return legacy_path if legacy_path.is_file() else None


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if not METADATA_PATH.is_file():
        OUT_PATH.write_text(
            json.dumps(
                error_response("Error: submission_metadata.json not found."),
            ),
            encoding="utf-8",
        )
        return

    meta = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    manifest: dict | None = None
    if MANIFEST_PATH.is_file():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"Invalid precomputed_manifest.json: {e}", file=sys.stderr)

    if manifest and int(manifest.get("schema_version", 0)) == MANIFEST_SCHEMA_VERSION:
        ok, diag = _assignment_invariant_ok(manifest, meta)
        if not ok:
            OUT_PATH.write_text(
                json.dumps(
                    error_response(
                        "Autograder package does not match this assignment. "
                        "Re-download the autograder ZIP from the AI autograder export "
                        "for this course/assignment and upload it in Configure Autograder."
                    ),
                ),
                encoding="utf-8",
            )
            return

    match_path = resolve_precomputed_path(meta, manifest)

    if match_path:
        shutil.copy(match_path, OUT_PATH)
        return

    users = meta.get("users") or []
    key = canonical_submitter_key_from_runtime_users(
        users if isinstance(users, list) else []
    )
    ids_for_msg = []
    if isinstance(users, list):
        for u in users:
            if isinstance(u, dict) and u.get("id") is not None:
                ids_for_msg.append(str(u["id"]))
    print(
        f"precomputed lookup miss: submitter_key={key!r} user_ids={ids_for_msg!r}",
        file=sys.stderr,
    )
    student_name = (users[0].get("name", "") or "").strip() if users else ""
    msg = (
        "No pre-computed results for this submission. "
        "If your instructor released grades, they may need to re-run export and re-upload the autograder ZIP."
    )
    if student_name:
        msg = f"{msg} (name={student_name!r})"
    OUT_PATH.write_text(json.dumps(error_response(msg)), encoding="utf-8")


if __name__ == "__main__":
    main()
