"""Gather student submissions from a Gradescope export ZIP or extracted folder."""

import argparse
import shutil
import zipfile
from pathlib import Path

import yaml  # Used for Gradescope submission_metadata.yml (Ruby-style keys)

from utils import load_config as _load_config


def load_submission_metadata(metadata_path: Path) -> dict:
    """Load submission_metadata.yml. Handles both Ruby-style (:key) and plain keys."""
    with open(metadata_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        return {}
    return raw


def get_student_name(entry: dict) -> str | None:
    """Extract student name from a submission entry."""
    submitters = entry.get(":submitters") or entry.get("submitters") or []
    if not submitters:
        return None
    first = submitters[0]
    if isinstance(first, dict):
        return first.get(":name") or first.get("name")
    return None


def gather_submissions(
    source: Path,
    out_dir: Path,
    *,
    from_zip: bool = False,
) -> list[dict]:
    """
    Gather student notebooks from a Gradescope export.

    Args:
        source: Path to either (a) an extracted folder containing submission_metadata.yml,
                or (b) a .zip file (when from_zip=True)
        out_dir: Directory to copy renamed notebooks into
        from_zip: If True, source is a ZIP; extract to temp, process, then clean up

    Returns:
        List of {"student_name", "filename", "status": "ok"|"missing"|"duplicate"}
    """
    extract_root: Path | None = None
    if from_zip:
        extract_root = source.parent / f"_extract_{source.stem}"
        try:
            with zipfile.ZipFile(source, "r") as zf:
                zf.extractall(extract_root)
        except Exception:
            shutil.rmtree(extract_root, ignore_errors=True)
            raise
        # Find the assignment_*_export folder or folder containing submission_metadata.yml
        export_dirs = list(extract_root.glob("assignment_*_export"))
        if export_dirs:
            base_dir = export_dirs[0]
        elif (extract_root / "submission_metadata.yml").exists():
            base_dir = extract_root
        else:
            base_dir = extract_root
    else:
        base_dir = source

    metadata_path = base_dir / "submission_metadata.yml"
    if not metadata_path.exists():
        if from_zip and extract_root:
            shutil.rmtree(extract_root, ignore_errors=True)
        return []

    data = load_submission_metadata(metadata_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    seen_names: set[str] = set()
    results: list[dict] = []

    for sub_key, sub_data in data.items():
        if not isinstance(sub_data, dict):
            continue
        student_name = get_student_name(sub_data)
        if not student_name:
            results.append(
                {
                    "student_name": sub_key,
                    "filename": "",
                    "status": "missing",
                    "message": "No submitter name in metadata",
                }
            )
            continue

        # Folder: submission_381579075 -> submission_381579075
        folder_name = sub_key
        submission_folder = base_dir / folder_name

        if not submission_folder.exists():
            results.append(
                {
                    "student_name": student_name,
                    "filename": "",
                    "status": "missing",
                    "message": f"Folder {folder_name} not found",
                }
            )
            continue

        ipynb_files = list(submission_folder.glob("*.ipynb"))
        if len(ipynb_files) == 0:
            results.append(
                {
                    "student_name": student_name,
                    "filename": "",
                    "status": "missing",
                    "message": f"No .ipynb in {folder_name}",
                }
            )
            continue
        if len(ipynb_files) > 1:
            results.append(
                {
                    "student_name": student_name,
                    "filename": "",
                    "status": "duplicate",
                    "message": f"Multiple .ipynb files in {folder_name}",
                }
            )
            continue

        nb_path = ipynb_files[0]
        new_name = f"{student_name}_{nb_path.name}"
        safe_name = (
            "".join(c for c in new_name if c not in '/\\:*?"<>|') or "unknown_student"
        )
        dest_path = out_dir / safe_name

        status = "ok"
        if student_name in seen_names:
            status = "duplicate"
        seen_names.add(student_name)

        if status == "ok":
            shutil.copy2(nb_path, dest_path)

        results.append(
            {
                "student_name": student_name,
                "filename": safe_name,
                "status": status,
                "message": (
                    "" if status == "ok" else f"Duplicate submitter: {student_name}"
                ),
            }
        )

    if from_zip and extract_root:
        shutil.rmtree(extract_root, ignore_errors=True)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Gather student notebooks from Gradescope export"
    )
    parser.add_argument("--zip", type=Path, help="Path to Gradescope export ZIP")
    parser.add_argument("--folder", type=Path, help="Path to extracted export folder")
    parser.add_argument(
        "--out", type=Path, default=Path("output/submissions"), help="Output directory"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Config file for output path",
    )
    args = parser.parse_args()

    # Override out_dir from config if available
    out_dir = args.out
    if args.config.exists():
        cfg = _load_config(args.config)
        if cfg and "submissions_dir" in cfg:
            out_dir = Path(cfg["submissions_dir"])

    if args.zip:
        results = gather_submissions(args.zip, out_dir, from_zip=True)
    elif args.folder:
        results = gather_submissions(args.folder, out_dir, from_zip=False)
    else:
        print("Provide --zip or --folder")
        return 1

    for r in results:
        status_icon = "✓" if r["status"] == "ok" else "✗"
        print(f"{status_icon} {r['student_name']}: {r['filename']} ({r['status']})")
    print(
        f"\nGathered {sum(1 for r in results if r['status'] == 'ok')} notebooks to {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
