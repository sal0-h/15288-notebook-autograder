#!/usr/bin/env python3
"""One-off: move raw cohort files into _raw/, copy solution.ipynb (no JSON rewrite),
build human_grades.csv aligned to parsed question IDs, write config.yaml.

Designed for hand-canonicalized experiment_data labs (e.g. S24) with dash-font prompts
and a 2-capture question_regex."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import shutil
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config_models import ensure_app_config, sort_key_qid
from parse_notebook import get_all_question_ids, parse_notebook


def _load_build_module():
    path = PROJECT_ROOT / "scripts" / "build_experiment_layout.py"
    spec = importlib.util.spec_from_file_location("build_experiment_layout", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def question_ids_parse_order(parsed: dict) -> list[str]:
    """Section order (numeric when possible), then question id order within section."""
    out: list[str] = []
    def sec_key(s: str) -> tuple:
        return (0, int(s)) if str(s).isdigit() else (1, s)

    for sec_id in sorted(parsed["sections"].keys(), key=sec_key):
        qs = parsed["sections"][sec_id]["questions"]
        for qid in sorted(qs.keys(), key=sort_key_qid):
            out.append(qid)
    return out


def link_or_copy_submissions(lab_dir: Path, raw: Path) -> None:
    """Expose ``submissions/`` at lab root (README canonical layout)."""
    src = raw / "submissions"
    if not src.is_dir():
        raise FileNotFoundError(f"Missing {src}")
    dst = lab_dir / "submissions"
    if dst.is_symlink() or dst.exists():
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        else:
            shutil.rmtree(dst)
    try:
        dst.symlink_to(src.resolve(), target_is_directory=True)
    except OSError:
        shutil.copytree(src, dst)


def ensure_raw(lab_dir: Path) -> Path:
    raw = lab_dir / "_raw"
    raw.mkdir(parents=True, exist_ok=True)
    for name in ("grades.csv", "metadata.yml"):
        src = lab_dir / name
        if src.is_file() and src.parent == lab_dir:
            dst = raw / name
            if not dst.exists():
                shutil.move(str(src), dst)
    sub = lab_dir / "submissions"
    if sub.is_dir() and sub.parent == lab_dir:
        dst_sub = raw / "submissions"
        if not dst_sub.exists():
            shutil.move(str(sub), dst_sub)
    for sol in list(lab_dir.glob("*_sol.ipynb")):
        if sol.parent == lab_dir:
            dst = raw / sol.name
            if not dst.exists():
                shutil.move(str(sol), dst)
    return raw


def find_raw_solution(raw: Path) -> Path:
    cands = sorted(raw.glob("*_sol.ipynb"))
    if not cands:
        raise FileNotFoundError(f"No *_sol.ipynb under {raw}")
    if len(cands) > 1:
        raise ValueError(f"Multiple *_sol.ipynb in {raw}: {cands}")
    return cands[0]


def chunk_groups(qids: list[str], size: int = 6) -> list[list[str]]:
    return [qids[i : i + size] for i in range(0, len(qids), size)]


def canonicalize(
    lab_dir: Path,
    *,
    assignment_name: str | None = None,
    section_regex: str,
    question_regex: str,
    allow_misaligned_human: bool = False,
) -> None:
    lab_dir = lab_dir.resolve()
    raw = ensure_raw(lab_dir)
    sol_src = find_raw_solution(raw)
    shutil.copy2(sol_src, lab_dir / "solution.ipynb")

    link_or_copy_submissions(lab_dir, raw)

    grades_path = raw / "grades.csv"
    if not grades_path.is_file():
        raise FileNotFoundError(f"Missing {grades_path}")

    build = _load_build_module()
    with open(grades_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    if allow_misaligned_human:
        print(
            f"WARNING {lab_dir.name}: --allow-misaligned-human — "
            "human_grades per-question cells will be EMPTY (totals copied only). "
            "Replace _raw/*_sol.ipynb or grades.csv when you have a matching export.",
            file=sys.stderr,
        )
        q_columns = []
    else:
        q_columns = build._parse_question_columns(headers)
        if not q_columns:
            raise ValueError(f"No question columns parsed from {grades_path}")

    cfg_dict = {
        "assignment_name": assignment_name
        or f"S24_{lab_dir.name}",
        "model": "gpt-4.1",
        "rubric_model": "",
        "rubric_review": True,
        "include_reference_in_grading": False,
        "solution_notebook": "solution.ipynb",
        "output_dir": "output",
        "workers": 32,
        "max_prompt_tokens": 80000,
        "max_completion_tokens": 8192,
        "parsing": {
            "section_regex": section_regex,
            "question_regex": question_regex,
            "keep_images": True,
        },
        "grading": {
            "question_groups": [],
            "grade_only": None,
            "grade_only_merge": False,
        },
        "rubrics": {},
    }
    cfg = ensure_app_config(cfg_dict)
    parsed = parse_notebook(lab_dir / "solution.ipynb", cfg)
    parse_order = question_ids_parse_order(parsed)

    dual_map_mode = build.gradescope_notebook_dual_headers_present(headers)

    if allow_misaligned_human:
        sorted_qids = sorted(parse_order, key=sort_key_qid)
        cfg_dict["grading"]["question_groups"] = chunk_groups(parse_order, 6)
        (lab_dir / "config.yaml").write_text(
            yaml.safe_dump(cfg_dict, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        extra_meta = ["Total Score", "Max Points"]
        fields = ["anon_id"] + extra_meta + sorted_qids
        out_csv = lab_dir / "human_grades.csv"
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                out_row: dict[str, str] = {
                    "anon_id": str(row.get("anon_id", "")).strip(),
                }
                for m in extra_meta:
                    out_row[m] = str(row.get(m, "")).strip()
                for qid in sorted_qids:
                    out_row[qid] = ""
                w.writerow(out_row)
        cfg2 = ensure_app_config(
            yaml.safe_load((lab_dir / "config.yaml").read_text(encoding="utf-8"))
        )
        cfg2 = cfg2.model_copy(
            update={
                "solution_notebook": str((lab_dir / "solution.ipynb").resolve()),
            }
        )
        parsed2 = parse_notebook(Path(cfg2.solution_notebook), cfg2)
        got = set(get_all_question_ids(parsed2))
        want = set(sorted_qids)
        if not want.issubset(got):
            raise ValueError(
                f"Post-write parse missing qids: {sorted(want - got)[:20]}"
            )
        return

    col_by_qid: dict[str, str]
    if dual_map_mode:
        col_by_qid = {}
        for spec, orig in q_columns:
            qid = spec.qid
            if qid in col_by_qid:
                raise ValueError(
                    f"{lab_dir.name}: duplicate notebook QID {qid!r} in grades.csv "
                    f"columns {col_by_qid[qid]!r} and {orig!r} (Gradescope "
                    f"``<g>: <n> (pts)`` mapping)."
                )
            col_by_qid[qid] = orig
        missing_csv = [q for q in parse_order if q not in col_by_qid]
        if missing_csv:
            print(
                f"WARNING {lab_dir.name}: {len(missing_csv)} parsed question(s) have "
                f"no grades.csv column (human cells left empty): "
                f"{missing_csv[:15]}{'…' if len(missing_csv) > 15 else ''}",
                file=sys.stderr,
            )
        extra_csv = sorted(set(col_by_qid) - set(parse_order), key=sort_key_qid)
        if extra_csv:
            print(
                f"WARNING {lab_dir.name}: grades.csv has notebook QIDs not present in "
                f"solution parse (ignored for human_grades): "
                f"{extra_csv[:15]}{'…' if len(extra_csv) > 15 else ''}",
                file=sys.stderr,
            )
    else:
        if len(q_columns) > len(parse_order):
            dropped = len(q_columns) - len(parse_order)
            if dropped > 4:
                raise ValueError(
                    f"{lab_dir.name}: parse has {len(parse_order)} questions but "
                    f"grades.csv has {len(q_columns)} — refusing to drop {dropped} "
                    f"columns automatically (limit 4). Fix notebook/CSV alignment."
                )
            print(
                f"WARNING {lab_dir.name}: truncating CSV question columns "
                f"from {len(q_columns)} to {len(parse_order)} (tail columns dropped)"
            )
            q_columns = q_columns[: len(parse_order)]
        elif len(parse_order) > len(q_columns):
            excess = len(parse_order) - len(q_columns)
            if excess > 25:
                raise ValueError(
                    f"{lab_dir.name}: parse has {len(parse_order)} questions, "
                    f"CSV has {len(q_columns)} — excess {excess} (limit 25 for "
                    f"automatic parse tail trim). Fix notebook or CSV."
                )
            print(
                f"WARNING {lab_dir.name}: truncating parse tail by {excess} questions "
                f"to match CSV; setting grading.grade_only"
            )
            parse_order = parse_order[: len(q_columns)]
            cfg_dict["grading"]["grade_only"] = list(parse_order)

        if len(parse_order) != len(q_columns):
            raise ValueError(
                f"{lab_dir.name}: internal mismatch parse_order vs q_columns "
                f"{len(parse_order)} vs {len(q_columns)}"
            )

        col_by_qid = {parse_order[i]: orig for i, (_, orig) in enumerate(q_columns)}

    sorted_qids = sorted(parse_order, key=sort_key_qid)

    cfg_dict["grading"]["question_groups"] = chunk_groups(parse_order, 6)
    (lab_dir / "config.yaml").write_text(
        yaml.safe_dump(cfg_dict, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    extra_meta = ["Total Score", "Max Points"]
    fields = ["anon_id"] + extra_meta + sorted_qids
    out_csv = lab_dir / "human_grades.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            out_row = {
                "anon_id": str(row.get("anon_id", "")).strip(),
            }
            for m in extra_meta:
                out_row[m] = str(row.get(m, "")).strip()
            for qid in sorted_qids:
                col = col_by_qid.get(qid)
                out_row[qid] = (
                    str(row.get(col, "")).strip() if col is not None else ""
                )
            w.writerow(out_row)

    # Verify
    cfg2 = ensure_app_config(
        yaml.safe_load((lab_dir / "config.yaml").read_text(encoding="utf-8"))
    )
    cfg2 = cfg2.model_copy(
        update={
            "solution_notebook": str((lab_dir / "solution.ipynb").resolve()),
        }
    )
    parsed2 = parse_notebook(Path(cfg2.solution_notebook), cfg2)
    got = set(get_all_question_ids(parsed2))
    want = set(sorted_qids)
    if not want.issubset(got):
        raise ValueError(
            f"Post-write parse missing qids: {sorted(want - got)[:20]}"
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lab-dir", type=Path, required=True)
    ap.add_argument("--assignment-name", type=str, default=None)
    ap.add_argument(
        "--section-regex",
        default=r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)(?!\.)",
        help="CMU # <font> section header; (?!\\.) avoids matching subsection lines like "
        "'# <font>1.1 Title' as section 1 (would duplicate section ids).",
    )
    ap.add_argument(
        "--question-regex",
        default=r"(?im)^\s*-?\s*(\d+)\s*<font[^>]*>.*?\[\s*(\d+)\s*pts?\s*\]",
        help="Dash optional: '- 1 <font>… [pts]' or '1 <font>… [pts]' (2 captures; section from header)",
    )
    ap.add_argument(
        "--allow-misaligned-human",
        action="store_true",
        help="When grades.csv questions do not match the solution notebook, still write "
        "config + human_grades with empty per-q cells (totals from CSV only).",
    )
    args = ap.parse_args()
    canonicalize(
        args.lab_dir,
        assignment_name=args.assignment_name,
        section_regex=args.section_regex,
        question_regex=args.question_regex,
        allow_misaligned_human=args.allow_misaligned_human,
    )
    print(f"OK {args.lab_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
