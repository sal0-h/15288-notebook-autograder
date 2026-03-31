"""Gradescope submitter identity: canonical keys for export ZIP and autograder runtime.

Gradescope's autograder ``submission_metadata.json`` documents a ``users`` array with
``id``, ``email``, and ``name`` per submitter (see Gradescope autograder docs). Course
ZIP exports use ``submission_metadata.yml`` with Ruby-style keys; submitter entries
may include ``:id`` alongside ``:name``. When ``:id`` is present on every submitter
in a submission, we build the same canonical key the runtime uses from JSON ``users``.
"""

from __future__ import annotations

MANIFEST_SCHEMA_VERSION = 1


def _normalize_id_value(uid: object) -> str | None:
    if uid is None:
        return None
    if isinstance(uid, bool):
        return None
    if isinstance(uid, int):
        return str(uid)
    if isinstance(uid, float):
        if uid != uid:  # NaN
            return None
        return str(int(uid)) if uid == int(uid) else str(uid)
    s = str(uid).strip()
    return s or None


def canonical_submitter_key_from_runtime_users(users: list[dict]) -> str | None:
    """Build submitter key from autograder ``submission_metadata.json`` ``users`` list.

    Single submitter: ``str(id)``. Group: comma-separated sorted ids.
    Returns None if ``users`` is empty or any user lacks ``id``.
    """
    if not users:
        return None
    ids: list[str] = []
    for u in users:
        if not isinstance(u, dict):
            return None
        sid = _normalize_id_value(u.get("id"))
        if sid is None:
            return None
        ids.append(sid)
    ids.sort()
    return ",".join(ids)


def submitter_key_from_yaml_submitters(submitters: list[dict]) -> str | None:
    """Build submitter key from Gradescope export YAML ``:submitters`` list.

    Returns None if the list is empty or any submitter lacks ``:id``.
    """
    if not submitters:
        return None
    ids: list[str] = []
    for s in submitters:
        if not isinstance(s, dict):
            return None
        sid = _normalize_id_value(s.get(":id"))
        if sid is None:
            return None
        ids.append(sid)
    ids.sort()
    return ",".join(ids)


def submitter_key_to_results_filename(key: str) -> str:
    """Map canonical key to a safe basename (no comma) for ``results/*.json`` in the ZIP."""
    return key.replace(",", "__")


def submitter_key_from_results_filename(stem: str) -> str:
    """Inverse of :func:`submitter_key_to_results_filename` for stems produced by that helper."""
    return stem.replace("__", ",")
