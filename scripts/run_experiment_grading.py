#!/usr/bin/env python3
"""Batch parse + grade for staged experiment labs (output/S*_LabTest_*).

Uses existing ``run_parse`` and ``grade_all_students`` (parallel workers, resume).
Before each (model, lab) run:

* If ``experiment_runs/<sanitize(incoming model)>/graded_results.json`` exists and
  covers every parsed student with ``_provenance.model`` matching that model, the lab
  is **skipped** (no API re-grade).
* If the same archive exists but is **incomplete**, it is copied to the assignment root,
  ``grade_all_students`` resumes missing students, then results are copied back to the
  archive and the root file is removed.
* If the root ``graded_results.json`` is already complete for the incoming model, it is
  **moved** into ``experiment_runs/<sanitize(incoming model)>/`` (replacing a partial
  archive there if present) and the lab is skipped.
* Otherwise any other non-empty root file is archived using provenance-based folder
  names (not the incoming loop model). Empty ``[]`` at root is left in place. Mixed
  provenance in a file being archived aborts that lab with an error.

Edit MODELS / DEFAULT_LABS at the bottom or pass ``--models`` / ``--labs``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from batch_grader import grade_all_students
from config_models import AppConfig, get_assignment_output_paths, load_app_config
from pipeline_runner import run_parse
from utils import setup_assignment_logging

# Default staged experiment assignments (prepare_assignment.py --all)
DEFAULT_LABS: tuple[str, ...] = (
    "S25_LabTest_2",
    "S25_LabTest_3",
    "S25_LabTest_4",
    "S25_LabTest_6",
    "S25_LabTest_7",
    "S26_LabTest_2",
    "S26_LabTest_1",
)

# Grading models to run (outer loop). Gianni: full + mini per family — edit here.
DEFAULT_MODELS: tuple[str, ...] = (
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-5",
    "gpt-5-mini",
)

DEFAULT_WORKERS = 32


def _sanitize_model_tag(model: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", model.strip())
    return safe or "model"


def _collect_provenance_models(rows: list) -> set[str]:
    """Distinct ``_provenance.model`` values from graded result rows."""
    models: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        qs = row.get("questions") or {}
        if not isinstance(qs, dict):
            continue
        for q in qs.values():
            if not isinstance(q, dict):
                continue
            prov = q.get("_provenance")
            if isinstance(prov, dict):
                m = prov.get("model")
                if m is not None and str(m).strip():
                    models.add(str(m).strip())
    return models


def _infer_archive_tag(
    graded_path: Path, *, fallback_model: str, log: logging.Logger
) -> str | None:
    """Return sanitized folder tag for ``graded_results.json``, or None to skip archiving."""
    if not graded_path.is_file():
        return None
    try:
        raw = json.loads(graded_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"Cannot archive {graded_path}: invalid JSON ({e})") from e
    if not isinstance(raw, list):
        raise ValueError(f"Cannot archive {graded_path}: expected a JSON array")
    if len(raw) == 0:
        return None

    models = _collect_provenance_models(raw)
    if len(models) > 1:
        raise ValueError(
            f"Cannot archive {graded_path}: mixed _provenance.model values {sorted(models)}"
        )
    if len(models) == 1:
        return _sanitize_model_tag(next(iter(models)))

    fb = (fallback_model or "").strip()
    if not fb:
        raise ValueError(
            f"Cannot archive {graded_path}: no _provenance.model on any question and "
            "config model is empty — fix the file or set model in config.yaml"
        )
    log.warning(
        "No _provenance.model in %s; archiving under config model %r",
        graded_path,
        fb,
    )
    return _sanitize_model_tag(fb)


def _archive_prior_graded_results(cfg, log: logging.Logger) -> Path | None:
    """Move graded_results.json aside using provenance-based folder name."""
    paths = get_assignment_output_paths(cfg)
    src = paths.graded_results
    tag = _infer_archive_tag(src, fallback_model=cfg.model or "", log=log)
    if tag is None:
        return None
    dest_dir = paths.output_dir / "experiment_runs" / tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "graded_results.json"
    if dest.exists():
        log.warning("Replacing existing archive at %s", dest)
        dest.unlink()
    shutil.move(str(src), str(dest))
    return dest


def _incoming_model_archive_path(cfg: AppConfig, incoming_model: str) -> Path:
    paths = get_assignment_output_paths(cfg)
    tag = _sanitize_model_tag(incoming_model)
    return paths.output_dir / "experiment_runs" / tag / "graded_results.json"


def _parsed_stems(cfg: AppConfig) -> set[str]:
    paths = get_assignment_output_paths(cfg)
    d = paths.parsed_dir
    if not d.is_dir():
        return set()
    return {p.stem for p in d.glob("*.json")}


def _graded_stems(rows: list) -> set[str]:
    stems: set[str] = set()
    for row in rows:
        if isinstance(row, dict) and row.get("student_name"):
            stems.add(str(row["student_name"]))
    return stems


def _load_graded_rows(path: Path) -> list | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, list):
        return None
    return raw


def _provenance_ok_for_incoming(
    rows: list, incoming_model: str
) -> tuple[bool, str | None]:
    """True if file is safe to treat as this model's run (single provenance family)."""
    prov = _collect_provenance_models(rows)
    if len(prov) > 1:
        return False, f"mixed _provenance.model values {sorted(prov)}"
    if len(prov) == 1 and next(iter(prov)) != incoming_model.strip():
        return (
            False,
            f"provenance {next(iter(prov))!r} does not match incoming model {incoming_model!r}",
        )
    return True, None


def _grading_complete_for_incoming(
    path: Path, cfg: AppConfig, incoming_model: str
) -> bool:
    """Every parsed student has a row; provenance (if any) matches incoming model."""
    rows = _load_graded_rows(path)
    if rows is None or len(rows) == 0:
        return False
    ok, _ = _provenance_ok_for_incoming(rows, incoming_model)
    if not ok:
        return False
    parsed = _parsed_stems(cfg)
    graded = _graded_stems(rows)
    if parsed:
        return parsed <= graded
    return True


def _remove_stale_live_shadowing_incoming_archive(
    live: Path,
    arch: Path,
    cfg: AppConfig,
    incoming_model: str,
    log: logging.Logger,
) -> None:
    """Drop root ``graded_results.json`` if it duplicates the incoming model archive slot."""
    if not live.is_file() or not arch.is_file():
        return
    incoming_tag = _sanitize_model_tag(incoming_model)
    tag = _infer_archive_tag(live, fallback_model=cfg.model or "", log=log)
    if tag is None:
        return
    if tag == incoming_tag:
        log.warning(
            "Removing stale live graded_results.json (same archive slot as %s)", arch
        )
        live.unlink()


def _finalize_incoming_archive_from_live(
    live: Path, arch: Path, log: logging.Logger
) -> None:
    """After a resume run, persist live results back to the incoming-model archive."""
    if not live.is_file():
        return
    arch.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(live, arch)
    live.unlink()
    log.info("Synced graded_results.json -> %s and removed live copy", arch)


def _run_one_lab(
    lab: str,
    model: str,
    workers: int,
    *,
    skip_parse: bool,
    continue_on_error: bool,
) -> int:
    """Run parse (optional) + grade for one lab. Returns count of student errors."""
    config_path = PROJECT_ROOT / "output" / lab / "config.yaml"
    if not config_path.is_file():
        print(f"SKIP {lab}: missing {config_path}", file=sys.stderr)
        return 0

    cfg_base = load_app_config(config_path)
    out_dir = Path(cfg_base.output_dir)
    setup_assignment_logging(cfg_base.assignment_name, out_dir)
    log = logging.getLogger(__name__)

    paths = get_assignment_output_paths(cfg_base)
    live = paths.graded_results
    arch = _incoming_model_archive_path(cfg_base, model)

    _remove_stale_live_shadowing_incoming_archive(live, arch, cfg_base, model, log)

    if arch.is_file() and _grading_complete_for_incoming(arch, cfg_base, model):
        if live.is_file():
            log.info(
                "Removing redundant live graded_results.json (archive already complete)"
            )
            live.unlink()
        log.info("Skipping %s: archive complete for %s at %s", lab, model, arch)
        print(f"  [{lab}] skip (archive already complete for {model})")
        return 0

    if live.is_file() and _grading_complete_for_incoming(live, cfg_base, model):
        arch.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(live), str(arch))
        log.info("Moved complete graded_results.json -> %s", arch)
        print(f"  [{lab}] skip (moved complete live results to archive for {model})")
        return 0

    restored_from_archive = False
    if arch.is_file():
        rows = _load_graded_rows(arch)
        if rows is None:
            log.error("Invalid JSON in archive %s", arch)
            print(f"ERROR {lab}: invalid JSON in {arch}", file=sys.stderr)
            return 1
        ok_resume, err_msg = _provenance_ok_for_incoming(rows, model)
        if not ok_resume:
            log.error("%s", err_msg)
            print(f"ERROR {lab}: {err_msg}", file=sys.stderr)
            return 1
        if not _grading_complete_for_incoming(arch, cfg_base, model):
            try:
                archived_other = _archive_prior_graded_results(cfg_base, log)
            except ValueError as e:
                log.error("%s", e)
                print(f"ERROR {lab}: {e}", file=sys.stderr)
                return 1
            if archived_other:
                log.info("Archived prior graded_results.json -> %s", archived_other)
            shutil.copy2(arch, live)
            restored_from_archive = True
            log.info("Restored partial archive to %s for resume", live)
            print(f"  [{lab}] resume from partial archive ({model})")
    else:
        try:
            archived = _archive_prior_graded_results(cfg_base, log)
        except ValueError as e:
            log.error("%s", e)
            print(f"ERROR {lab}: {e}", file=sys.stderr)
            return 1
        if archived:
            log.info("Archived prior graded_results.json -> %s", archived)

    cfg = cfg_base.model_copy(update={"model": model, "workers": workers})
    if not (cfg.rubric_model or "").strip():
        cfg = cfg.model_copy(update={"rubric_model": model})

    if not skip_parse:
        payload = run_parse(cfg)
        n_ok = sum(1 for r in payload["report"] if r["status"] == "ok")
        log.info("Parse %s: %d students ok", lab, n_ok)

    errors = 0
    for evt in grade_all_students(cfg):
        st = evt.get("status")
        if st == "done" and evt.get("result"):
            r = evt["result"]
            print(
                f"  [{lab}] ✓ {r['student_name']}: {r['total_score']}/{r['total_max']}"
            )
        elif st == "error":
            errors += 1
            print(
                f"  [{lab}] ✗ {evt.get('student')}: {evt.get('error')}", file=sys.stderr
            )
            if not continue_on_error:
                log.error("Stopping after first grading error (%s)", lab)
                break
        elif st == "queue_info" and evt.get("message"):
            log.info("[%s] queue: %s", lab, evt["message"])
        elif st == "usage":
            log.info("[%s] usage: %s", lab, evt)

    if restored_from_archive:
        _finalize_incoming_archive_from_live(live, arch, log)

    if errors:
        log.warning("[%s] grading finished with %d student error(s)", lab, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse + grade all experiment labs for one or more models.",
    )
    parser.add_argument(
        "--models",
        type=str,
        default=",".join(DEFAULT_MODELS),
        help=f"Comma-separated model ids (default: {','.join(DEFAULT_MODELS)})",
    )
    parser.add_argument(
        "--labs",
        type=str,
        default=",".join(DEFAULT_LABS),
        help=f"Comma-separated assignment_name under output/ (default: all staged labs)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel workers per lab (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="Only run grade (parsed + solution_parsed must already exist).",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep going after a student grading failure (default: stop on first error).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    labs = [L.strip() for L in args.labs.split(",") if L.strip()]
    if not models or not labs:
        print("No models or no labs to run.", file=sys.stderr)
        return 2

    total_errors = 0
    for model in models:
        print(f"\n=== Model {model} ===", flush=True)
        for lab in labs:
            print(f"\n--- {lab} ---", flush=True)
            err = _run_one_lab(
                lab,
                model,
                args.workers,
                skip_parse=args.skip_parse,
                continue_on_error=args.continue_on_error,
            )
            total_errors += err
            if err and not args.continue_on_error:
                print(
                    f"\nStopped: {err} error(s) in {lab} (use --continue-on-error to keep going).",
                    file=sys.stderr,
                )
                return 1

    if total_errors:
        print(f"\nDone with {total_errors} student-level error(s).", file=sys.stderr)
        return 1
    print("\nAll labs finished with no student-level grading errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
