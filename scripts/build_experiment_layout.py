#!/usr/bin/env python3
"""Build a canonical experiment_data layout from raw cohort dumps.

Canonical lab folder (output of this script):

    experiment_data/<cohort>/<lab>/
        _raw/                       # untouched originals (created on first run)
        config.yaml                 # created only if missing (see --overwrite-config)
        solution.ipynb              # rewritten so default Q-style regex matches
        human_grades.csv            # anon_id, total, max, then qid columns (1.1, 1.2 ...)
        submissions/<NNN>.ipynb     # one notebook per anon stem; matches human_grades.csv

S24 / S25 inputs (per lab, under ``experiment_data/S24`` or ``.../S25``):
    LabTest_<N>_{S24|S25}_sol.ipynb, grades.csv (anon_id), submissions/<NNN>.ipynb, metadata.yml
    Question prompts use a dash-number pattern; we inject 'Q<sec>.<q> [<pts> PTS]'
    headers using grades.csv as the source of truth so the default pipeline regex
    parses every cell correctly.

S26 inputs (under experiment_data/S26):
    For lab number ``N`` (e.g. 1 → ``LabTest_1``): ``ltN.csv`` (raw Gradescope export),
    ``ltN.ipynb`` (solution), ``ltN.zip`` (Gradescope submissions ZIP). On first run
    these move into ``LabTest_N/_raw/``. The ZIP is extracted; submissions are re-keyed
    to ``001.ipynb``, … ordered by Submission ID; ``anon_id`` matches those stems.

The script is idempotent: it moves originals into _raw/ on first run and rebuilds
canonical artifacts every time (overwriting prior outputs). ``config.yaml`` is
written only when missing unless you pass ``--overwrite-config``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENT_ROOT = PROJECT_ROOT / "experiment_data"

# Header shapes seen across cohorts:
#   S25 LabTest_2/3/4 grades.csv: '<sec>.<q>: Question <q> (<pts> pts)'
#   S26 lt2.csv:                  '<col_idx>: <sec>.<q> (<pts> pts)'
#   S25 LabTest_5/6/7:            '<col_idx>: q<n> (<pts> pts)' / '<col_idx>: Q<n> ...' (no sec.qnum)
#   S24 LabTest_5 (Gradescope):   '<gs_sec>.<gs_q>: <notebook_sec>.<notebook_q> (<pts> pts)'
#       The first token is the Gradescope column id; the second is the notebook QID used by
#       parse_notebook — use the notebook id for human_grades alignment (see canonicalize).
# Rule: if every question column parses to unique <sec>.<qnum> we keep it; otherwise we
# synthesize flat '1.<idx>' so every lab is internally consistent.
_DUAL_GS_NB_HEADER = re.compile(
    r"^\s*(?P<gs>\d+\.\d+)\s*:\s*(?P<nb>\d+\.\d+)\s*\(\s*(?P<pts>[\d.]+)\s*pts?\s*\)",
    re.IGNORECASE,
)
_QID_PREFIX = re.compile(r"^\s*(\d+\.\d+)\s*:\s*")
_QID_TOKEN = re.compile(r"^\s*\d+\s*:\s*(\d+\.\d+)\s*\(")
_PTS = re.compile(r"\(([\d.]+)\s*pts?\)", re.IGNORECASE)

# Columns that are never per-question (Gradescope export + cohort additions).
_META_COLS = frozenset(
    {
        "anon_id",
        "Total Score",
        "Max Points",
        "Status",
        "Submission Time",
        "Lateness (H:M:S)",
        "View Count",
        "Submission Count",
        "Submission ID",
        "First Name",
        "Last Name",
        "SID",
        "Email",
        "Sections",
    }
)

# S25 prompt cell pattern (dash-leading number followed by <font> [N pts]).
_S25_DASH_PROMPT = re.compile(r"(?im)^\s*-+\s*\d+\s*<font[^>]*>.*?\[\s*\d+\s*pts?\s*\]")


@dataclass(frozen=True)
class QSpec:
    sec: int
    qnum: int
    pts: int

    @property
    def qid(self) -> str:
        return f"{self.sec}.{self.qnum}"


def _is_question_column(header: str) -> bool:
    if header in _META_COLS:
        return False
    if "Autograder" in header:
        return False
    return True


def gradescope_notebook_dual_headers_present(headers: list[str]) -> bool:
    """True if any question column uses ``<g.g>: <n.n> (pts)`` (notebook QID after colon)."""
    return any(
        _is_question_column(h) and _DUAL_GS_NB_HEADER.match(h.strip())
        for h in headers
    )


def _parse_question_columns(headers: list[str]) -> list[tuple[QSpec, str]]:
    """Ordered list of (canonical QSpec, original column name) for question columns.

    If every column parses to a unique <sec>.<qnum>, we use those ids verbatim.
    Otherwise we synthesize a flat '1.<idx>' scheme so the canonical layout is
    consistent for that lab even when the Gradescope labels were ad-hoc.

    For ``<gradescope_qid>: <notebook_qid> (pts)`` headers, the stored QSpec / qid is the
    **notebook** id (after the colon) so it matches ``parse_notebook`` output.
    """
    raw: list[tuple[str, str | None, int]] = []
    for h in headers:
        if not _is_question_column(h):
            continue
        dual = _DUAL_GS_NB_HEADER.match(h.strip())
        if dual:
            qid = dual.group("nb")
            pts = int(round(float(dual.group("pts"))))
            raw.append((h, qid, pts))
            continue
        m = _QID_PREFIX.match(h) or _QID_TOKEN.match(h)
        qid = m.group(1) if m else None
        pts_m = _PTS.search(h)
        pts = int(round(float(pts_m.group(1)))) if pts_m else 0
        raw.append((h, qid, pts))

    if not raw:
        return []

    qids = [q for _, q, _ in raw]
    unique_natural = all(q is not None for q in qids) and len(set(qids)) == len(qids)
    out: list[tuple[QSpec, str]] = []
    if unique_natural:
        for orig, qid, pts in raw:
            assert qid is not None
            sec_s, q_s = qid.split(".")
            out.append((QSpec(int(sec_s), int(q_s), pts), orig))
    else:
        for idx, (orig, _qid, pts) in enumerate(raw, start=1):
            out.append((QSpec(1, idx, pts), orig))
    return out


# ---------------------------------------------------------------------------
# S25 prompt-cell rewriting
# ---------------------------------------------------------------------------


def _inject_qheader(cell_source: str, spec: QSpec) -> str:
    """Prepend a 'Q<sec>.<q> [<pts> PTS]' line so the default question_regex matches."""
    line = f"Q{spec.qid} [{spec.pts} PTS]\n"
    return line + cell_source


def _count_dash_prompts(nb_path: Path) -> int:
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    n = 0
    for cell in nb.get("cells", []):
        if cell.get("cell_type") != "markdown":
            continue
        src = "".join(cell.get("source", []))
        if _S25_DASH_PROMPT.search(src):
            n += 1
    return n


def _rewrite_s25_notebook(nb_path: Path, qspecs: list[QSpec]) -> dict:
    """Return the notebook dict with prompt cells rewritten in order."""
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    cells = nb.get("cells", [])
    qi = 0
    for cell in cells:
        if cell.get("cell_type") != "markdown":
            continue
        src_list = cell.get("source", [])
        src = "".join(src_list)
        if not _S25_DASH_PROMPT.search(src):
            continue
        if qi >= len(qspecs):
            break
        new_src = _inject_qheader(src, qspecs[qi])
        cell["source"] = new_src.splitlines(keepends=True)
        qi += 1
    nb["_experiment_data_qheader_rewrites"] = qi
    return nb


# ---------------------------------------------------------------------------
# Canonical artifact writers
# ---------------------------------------------------------------------------


_CANONICAL_PARSING = {
    "section_regex": r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
    "question_regex": r"(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
    "keep_images": True,
}

# Defaults for newly created experiment_data/*/config.yaml only (existing file is never overwritten).
DEFAULT_EXPERIMENT_MODEL = "gpt-4.1"
DEFAULT_EXPERIMENT_WORKERS = 32


def _config_yaml(assignment_name: str) -> str:
    cfg = {
        "assignment_name": assignment_name,
        "model": DEFAULT_EXPERIMENT_MODEL,
        "rubric_model": "",
        "rubric_review": False,
        "include_reference_in_grading": False,
        "solution_notebook": "solution.ipynb",
        "output_dir": "output",
        "workers": DEFAULT_EXPERIMENT_WORKERS,
        "max_prompt_tokens": 80000,
        "max_completion_tokens": 4096,
        "parsing": dict(_CANONICAL_PARSING),
        "grading": {
            "question_groups": [],
            "grade_only": None,
            "grade_only_merge": False,
        },
        "rubrics": {},
    }
    return yaml.safe_dump(cfg, sort_keys=False, default_flow_style=False)


def _write_config_yaml_if_needed(
    lab_dir: Path, assignment_name: str, *, overwrite: bool
) -> str:
    """Write template ``config.yaml`` if missing.

    Returns one of ``\"created\"``, ``\"skipped\"``, ``\"overwritten\"``.
    """
    path = lab_dir / "config.yaml"
    existed = path.is_file()
    if existed and not overwrite:
        return "skipped"
    path.write_text(_config_yaml(assignment_name), encoding="utf-8")
    return "overwritten" if existed else "created"


def _write_canonical_csv(
    out_path: Path,
    rows: list[dict[str, str]],
    qspecs: list[QSpec],
    extra_meta: list[str],
) -> None:
    """Write CSV: anon_id, extra_meta..., qid columns (sorted)."""
    qspec_by_qid = {s.qid: s for s in qspecs}
    ordered_qids = sorted(
        qspec_by_qid.keys(), key=lambda q: tuple(map(int, q.split(".")))
    )
    fields = ["anon_id"] + extra_meta + ordered_qids
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fields})


# ---------------------------------------------------------------------------
# S25 cohort builder
# ---------------------------------------------------------------------------


def _ensure_s25_raw(lab_dir: Path, cohort: str) -> Path:
    """Move originals to _raw/ if not already done. Returns _raw path.

    Expects solution notebook ``LabTest_*_{cohort}_sol.ipynb`` (e.g. S24, S25)
    at lab root before the first run.
    """
    raw = lab_dir / "_raw"
    if raw.is_dir() and (raw / "grades.csv").is_file():
        return raw
    raw.mkdir(parents=True, exist_ok=True)
    sol_candidates = list(lab_dir.glob(f"LabTest_*_{cohort}_sol.ipynb"))
    if sol_candidates:
        shutil.move(str(sol_candidates[0]), raw / "solution.ipynb")
    if (lab_dir / "grades.csv").is_file():
        shutil.move(str(lab_dir / "grades.csv"), raw / "grades.csv")
    if (lab_dir / "metadata.yml").is_file():
        shutil.move(str(lab_dir / "metadata.yml"), raw / "metadata.yml")
    if (lab_dir / "submissions").is_dir() and not (raw / "submissions").is_dir():
        shutil.move(str(lab_dir / "submissions"), raw / "submissions")
    return raw


def build_s25_lab(
    lab_dir: Path, *, cohort: str = "S25", overwrite_config: bool = False
) -> dict:
    """Build canonical layout for one lab folder using the S25-style raw dump.

    ``cohort`` is the tag in ``assignment_name`` (``{cohort}_LabTest_N``) and in
    the expected solution filename ``LabTest_*_{cohort}_sol.ipynb``.
    """
    raw = _ensure_s25_raw(lab_dir, cohort)

    grades_path = raw / "grades.csv"
    sol_raw = raw / "solution.ipynb"
    sub_raw = raw / "submissions"
    if not grades_path.is_file():
        raise FileNotFoundError(f"Missing {grades_path}")
    if not sol_raw.is_file():
        raise FileNotFoundError(f"Missing {sol_raw}")
    if not sub_raw.is_dir():
        raise FileNotFoundError(f"Missing {sub_raw}")

    with open(grades_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    q_columns = _parse_question_columns(headers)
    if not q_columns:
        raise ValueError(f"No question columns parsed from {grades_path}")

    sol_prompts = _count_dash_prompts(sol_raw)
    csv_qcount = len(q_columns)
    if sol_prompts != csv_qcount:
        keep = min(sol_prompts, csv_qcount)
        print(
            f"  WARNING {lab_dir.name}: solution prompts={sol_prompts} csv_questions={csv_qcount}; "
            f"keeping first {keep} for canonical layout (extras dropped)."
        )
        q_columns = q_columns[:keep]

    qspecs = [s for s, _ in q_columns]

    sol_nb = _rewrite_s25_notebook(sol_raw, qspecs)
    (lab_dir / "solution.ipynb").write_text(
        json.dumps(sol_nb, ensure_ascii=False), encoding="utf-8"
    )

    # Canonical submissions/NNN.ipynb (rewrite each in same order)
    out_sub = lab_dir / "submissions"
    if out_sub.exists():
        shutil.rmtree(out_sub)
    out_sub.mkdir(parents=True)
    raw_subs = sorted(sub_raw.glob("*.ipynb"))
    written = 0
    for nb in raw_subs:
        rewritten = _rewrite_s25_notebook(nb, qspecs)
        (out_sub / nb.name).write_text(
            json.dumps(rewritten, ensure_ascii=False), encoding="utf-8"
        )
        written += 1

    # Canonical human_grades.csv (anon_id + meta + qid columns)
    extra_meta = ["Total Score", "Max Points"]
    canonical_rows: list[dict[str, str]] = []
    for row in rows:
        canon: dict[str, str] = {
            "anon_id": str(row.get("anon_id", "")).strip(),
        }
        for m in extra_meta:
            canon[m] = str(row.get(m, "")).strip()
        for spec, orig_col in q_columns:
            canon[spec.qid] = str(row.get(orig_col, "")).strip()
        canonical_rows.append(canon)

    _write_canonical_csv(
        lab_dir / "human_grades.csv", canonical_rows, qspecs, extra_meta
    )

    assignment_name = f"{cohort}_{lab_dir.name}"
    config_status = _write_config_yaml_if_needed(
        lab_dir, assignment_name, overwrite=overwrite_config
    )

    return {
        "lab": lab_dir.name,
        "students": written,
        "questions": len(qspecs),
        "assignment_name": assignment_name,
        "config_yaml": config_status,
    }


# ---------------------------------------------------------------------------
# S26 cohort builder
# ---------------------------------------------------------------------------


def _normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def _ensure_s26_raw(s26_root: Path, lab_num: int) -> Path:
    """Move ``lt{lab_num}.*`` from S26 root into ``LabTest_{lab_num}/_raw/`` on first run."""
    lab_dir = s26_root / f"LabTest_{lab_num}"
    raw = lab_dir / "_raw"
    if raw.is_dir() and (raw / "grades.csv").is_file():
        return raw
    raw.mkdir(parents=True, exist_ok=True)
    lt_csv = s26_root / f"lt{lab_num}.csv"
    lt_ipynb = s26_root / f"lt{lab_num}.ipynb"
    lt_zip = s26_root / f"lt{lab_num}.zip"
    if lt_csv.is_file():
        shutil.move(str(lt_csv), raw / "grades.csv")
    if lt_ipynb.is_file():
        shutil.move(str(lt_ipynb), raw / "solution.ipynb")
    if lt_zip.is_file():
        shutil.move(str(lt_zip), raw / "submissions.zip")
    return raw


def _extract_s26_submissions(zip_path: Path, dest: Path) -> dict[str, Path]:
    """Extract Gradescope ZIP and return {gradescope_submission_id: notebook_path}."""
    out: dict[str, Path] = {}
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    export_dirs = list(dest.glob("assignment_*_export"))
    if len(export_dirs) != 1:
        raise ValueError(
            f"Expected exactly one assignment_*_export in {zip_path}, got {len(export_dirs)}"
        )
    export = export_dirs[0]
    for sub_dir in sorted(export.iterdir()):
        if not sub_dir.is_dir():
            continue
        # Expect names like submission_<gradescope_id>
        m = re.match(r"submission_(\d+)$", sub_dir.name)
        if not m:
            continue
        sid = m.group(1)
        ipynbs = list(sub_dir.glob("*.ipynb"))
        if len(ipynbs) != 1:
            continue
        out[sid] = ipynbs[0]
    return out


def build_s26_lab(
    s26_root: Path, lab_num: int, *, overwrite_config: bool = False
) -> dict:
    """Build canonical layout for S26 ``LabTest_{lab_num}`` (raw: ``lt{lab_num}.*``)."""
    raw = _ensure_s26_raw(s26_root, lab_num)
    lab_dir = raw.parent

    grades_path = raw / "grades.csv"
    sol_path = raw / "solution.ipynb"
    zip_path = raw / "submissions.zip"
    if not (grades_path.is_file() and sol_path.is_file() and zip_path.is_file()):
        raise FileNotFoundError(
            f"S26 lab requires grades.csv, solution.ipynb, submissions.zip under {raw}"
        )

    with open(grades_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    q_columns = _parse_question_columns(headers)
    if not q_columns:
        raise ValueError(f"No question columns parsed from {grades_path}")
    qspecs = [s for s, _ in q_columns]

    # Sort CSV rows deterministically: by Submission ID ascending; rows without an ID go last.
    def _sub_id(row: dict[str, str]) -> tuple[int, int]:
        raw_id = (row.get("Submission ID") or "").strip()
        if raw_id.isdigit():
            return (0, int(raw_id))
        return (1, 0)

    rows_sorted = sorted(rows, key=_sub_id)

    # Extract submissions
    with tempfile.TemporaryDirectory() as tmp:
        sid_to_nb = _extract_s26_submissions(zip_path, Path(tmp))
        out_sub = lab_dir / "submissions"
        if out_sub.exists():
            shutil.rmtree(out_sub)
        out_sub.mkdir(parents=True)

        canonical_rows: list[dict[str, str]] = []
        n_written = 0
        for idx, row in enumerate(rows_sorted, start=1):
            stem = f"{idx:03d}"
            sid = (row.get("Submission ID") or "").strip()
            nb_src = sid_to_nb.get(sid)
            if not nb_src:
                # Skip CSV rows with no matching notebook (e.g. missing submissions)
                continue
            shutil.copy2(nb_src, out_sub / f"{stem}.ipynb")

            canon: dict[str, str] = {
                "anon_id": stem,
                "Total Score": row.get("Total Score", ""),
                "Max Points": row.get("Max Points", ""),
            }
            for spec, orig in q_columns:
                canon[spec.qid] = row.get(orig, "")
            canonical_rows.append(canon)
            n_written += 1

    # Canonical solution.ipynb (S26 already uses Q-style headers; copy unchanged)
    shutil.copy2(sol_path, lab_dir / "solution.ipynb")

    extra_meta = ["Total Score", "Max Points"]
    _write_canonical_csv(
        lab_dir / "human_grades.csv", canonical_rows, qspecs, extra_meta
    )

    assignment_name = f"S26_{lab_dir.name}"
    config_status = _write_config_yaml_if_needed(
        lab_dir, assignment_name, overwrite=overwrite_config
    )

    return {
        "lab": f"S26/{lab_dir.name}",
        "students": n_written,
        "questions": len(qspecs),
        "assignment_name": assignment_name,
        "config_yaml": config_status,
    }


def _discover_s26_lab_nums(s26_root: Path) -> list[int]:
    """Lab indices ``N`` from ``ltN.zip`` at cohort root or existing ``LabTest_N/_raw``."""
    nums: set[int] = set()
    if not s26_root.is_dir():
        return []
    for p in s26_root.glob("lt*.zip"):
        m = re.match(r"lt(\d+)\.zip$", p.name, re.IGNORECASE)
        if m:
            nums.add(int(m.group(1)))
    for d in s26_root.glob("LabTest_*"):
        if not d.is_dir():
            continue
        m = re.match(r"LabTest_(\d+)$", d.name)
        if m and (d / "_raw" / "submissions.zip").is_file():
            nums.add(int(m.group(1)))
    return sorted(nums)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build canonical experiment_data layout (S24 + S25 + S26)."
    )
    ap.add_argument(
        "--root",
        type=Path,
        default=EXPERIMENT_ROOT,
        help="experiment_data root (default: repo experiment_data/)",
    )
    ap.add_argument(
        "--overwrite-config",
        action="store_true",
        help="Rewrite each lab's config.yaml from the template (default: keep existing)",
    )
    args = ap.parse_args()
    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    summary: list[dict] = []

    for cohort in ("S24", "S25"):
        cohort_root = root / cohort
        if not cohort_root.is_dir():
            continue
        for lab in sorted(
            p
            for p in cohort_root.iterdir()
            if p.is_dir() and p.name.startswith("LabTest_")
        ):
            try:
                summary.append(
                    build_s25_lab(
                        lab,
                        cohort=cohort,
                        overwrite_config=args.overwrite_config,
                    )
                )
                cfg_note = ""
                if summary[-1].get("config_yaml") == "skipped":
                    cfg_note = " (config.yaml unchanged)"
                print(
                    f"Built {cohort} {lab.name}: {summary[-1]['students']} students, "
                    f"{summary[-1]['questions']} qids{cfg_note}"
                )
            except Exception as e:
                print(f"FAILED {cohort} {lab.name}: {e}", file=sys.stderr)

    s26_root = root / "S26"
    for n in _discover_s26_lab_nums(s26_root):
        try:
            summary.append(
                build_s26_lab(s26_root, n, overwrite_config=args.overwrite_config)
            )
            cfg_note = ""
            if summary[-1].get("config_yaml") == "skipped":
                cfg_note = " (config.yaml unchanged)"
            print(
                f"Built {summary[-1]['lab']}: {summary[-1]['students']} students, {summary[-1]['questions']} qids{cfg_note}"
            )
        except Exception as e:
            print(f"FAILED S26 LabTest_{n}: {e}", file=sys.stderr)

    print(f"\n{len(summary)} lab(s) built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
