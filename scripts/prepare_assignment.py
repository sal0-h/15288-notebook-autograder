#!/usr/bin/env python3
"""Stage a canonical experiment_data lab into output/<assignment_name>/.

The autograder expects every assignment under ``output/<assignment_name>/``
(see config_models.load_app_config). This script materializes that runtime
directory by writing a small ``config.yaml`` and symlinking (or copying) the
canonical submissions and solution notebook from ``experiment_data/``.

After running, you can drive the pipeline directly:

    python main.py --steps parse --config output/<assignment_name>/config.yaml
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPERIMENT_ROOT = PROJECT_ROOT / "experiment_data"


def _iter_canonical_labs() -> list[Path]:
    labs: list[Path] = []
    if not EXPERIMENT_ROOT.is_dir():
        return labs
    for cohort in sorted(EXPERIMENT_ROOT.glob("S*")):
        if not cohort.is_dir():
            continue
        for lab in sorted(cohort.glob("LabTest_*")):
            if lab.is_dir() and (lab / "config.yaml").is_file():
                labs.append(lab)
    return labs


def _link_or_copy(src: Path, dst: Path, *, copy: bool) -> None:
    if dst.is_symlink() or dst.exists():
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
        elif dst.is_dir():
            shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    else:
        dst.symlink_to(src.resolve())


def prepare_assignment(lab_dir: Path, *, copy: bool = False) -> Path:
    lab_dir = lab_dir.resolve()
    cfg_path = lab_dir / "config.yaml"
    if not cfg_path.is_file():
        raise FileNotFoundError(
            f"{cfg_path} not found. Run scripts/build_experiment_layout.py first."
        )
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    name = str(cfg.get("assignment_name", "")).strip()
    if not name:
        raise ValueError(f"assignment_name missing in {cfg_path}")

    out_root = PROJECT_ROOT / "output" / name
    out_root.mkdir(parents=True, exist_ok=True)

    # Use absolute solution path so load_app_config resolves correctly regardless
    # of the runtime config location.
    solution_src = lab_dir / "solution.ipynb"
    submissions_src = lab_dir / "submissions"
    if not solution_src.is_file():
        raise FileNotFoundError(f"Missing {solution_src}")
    if not submissions_src.is_dir():
        raise FileNotFoundError(f"Missing {submissions_src}")

    cfg_runtime = dict(cfg)
    cfg_runtime["assignment_name"] = name
    cfg_runtime["solution_notebook"] = str(solution_src)

    (out_root / "config.yaml").write_text(
        yaml.safe_dump(cfg_runtime, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    _link_or_copy(submissions_src, out_root / "submissions", copy=copy)

    return out_root


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stage an experiment_data lab into output/<assignment_name>/."
    )
    ap.add_argument(
        "--lab-dir",
        type=Path,
        default=None,
        help="Path to a canonical lab folder, e.g. experiment_data/S25/LabTest_2",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="Stage every canonical lab under experiment_data/ (S*/LabTest_* with config.yaml).",
    )
    ap.add_argument(
        "--copy",
        action="store_true",
        help="Copy submissions instead of symlinking (slower, more disk).",
    )
    args = ap.parse_args()
    if args.all:
        labs = _iter_canonical_labs()
        if not labs:
            print("No canonical labs found under experiment_data/", file=sys.stderr)
            return 1
        for lab in labs:
            out = prepare_assignment(lab, copy=args.copy)
            print(
                f"Staged {lab.relative_to(PROJECT_ROOT)} -> {out.relative_to(PROJECT_ROOT)}"
            )
        print(f"\nStaged {len(labs)} lab(s).")
        return 0
    if args.lab_dir is None:
        ap.error("Provide --lab-dir or --all")
    if not args.lab_dir.is_dir():
        print(f"Not a directory: {args.lab_dir}", file=sys.stderr)
        return 1
    out = prepare_assignment(args.lab_dir, copy=args.copy)
    print(f"Staged {args.lab_dir} -> {out}")
    print(
        "Run: python main.py --steps parse "
        f"--config {out.relative_to(PROJECT_ROOT)}/config.yaml"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
