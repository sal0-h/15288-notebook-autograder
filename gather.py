"""Gather student submissions from a Gradescope export ZIP or extracted folder."""

import argparse
import shutil
import zipfile
from pathlib import Path

import yaml  # Used for Gradescope submission_metadata.yml (Ruby-style keys)

from config_models import load_app_config as _load_app_config
from utils import sanitize_filename_component


def load_submission_metadata(metadata_path: Path) -> dict:
    """Load Gradescope export ``submission_metadata.yml`` (Ruby-style symbol keys)."""
    with open(metadata_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_student_name(entry: dict) -> str | None:
    """Extract student name from a submission entry (Gradescope Rails export format)."""
    submitters = entry.get(":submitters") or []
    if not submitters:
        return None
    return submitters[0].get(":name")


def _result_entry(
    student_name: str,
    filename: str,
    status: str,
    message: str,
) -> dict:
    return {
        "student_name": student_name,
        "filename": filename,
        "status": status,
        "message": message,
    }


def _resolve_export_dir(source: Path, from_zip: bool) -> tuple[Path, Path | None]:
    """Return the extracted Gradescope export directory and optional temp root."""
    if not from_zip:
        return source, None

    extract_root = source.parent / f"_extract_{source.stem}"
    try:
        with zipfile.ZipFile(source, "r") as zf:
            zf.extractall(extract_root)
    except Exception:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise

    export_dirs = list(extract_root.glob("assignment_*_export"))
    if len(export_dirs) != 1:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise ValueError(
            "Expected exactly one assignment_*_export directory in Gradescope ZIP"
        )

    return export_dirs[0], extract_root


def _find_single_notebook(
    submission_folder: Path,
) -> tuple[Path, None, str] | tuple[None, str, str]:
    folder_name = submission_folder.name
    if not submission_folder.exists():
        return None, "missing", f"Folder {folder_name} not found"

    ipynb_files = list(submission_folder.glob("*.ipynb"))
    if len(ipynb_files) == 0:
        return None, "missing", f"No .ipynb in {folder_name}"
    if len(ipynb_files) > 1:
        return None, "duplicate", f"Multiple .ipynb files in {folder_name}"
    return ipynb_files[0], None, ""


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
    export_dir, extract_root = _resolve_export_dir(source, from_zip)
    try:
        metadata_path = export_dir / "submission_metadata.yml"
        if not metadata_path.exists():
            raise FileNotFoundError(
                f"submission_metadata.yml not found in Gradescope export: {export_dir}"
            )

        data = load_submission_metadata(metadata_path)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        seen_names: set[str] = set()
        results: list[dict] = []
        email_stem_map: dict[str, str] = {}  # email → notebook stem

        for sub_key, sub_data in data.items():
            if not isinstance(sub_data, dict):
                continue

            student_name = get_student_name(sub_data)
            if not student_name:
                results.append(
                    _result_entry(
                        sub_key,
                        "",
                        "missing",
                        "No submitter name in metadata",
                    )
                )
                continue

            submission_folder = export_dir / sub_key
            nb_path, failure_status, failure_message = _find_single_notebook(
                submission_folder
            )
            if nb_path is None:
                results.append(
                    _result_entry(
                        student_name,
                        "",
                        failure_status or "missing",
                        failure_message,
                    )
                )
                continue

            base_stem = sanitize_filename_component(
                student_name,
                if_empty="unknown_student",
            )
            safe_name = f"{base_stem}.ipynb"
            stem_only = base_stem

            status = "duplicate" if student_name in seen_names else "ok"
            seen_names.add(student_name)

            msg = "" if status == "ok" else f"Duplicate submitter: {student_name}"
            if status == "ok":
                shutil.copy2(nb_path, out_dir / safe_name)
                # Map email → stem for Gradescope autograder lookup
                submitters = (
                    sub_data.get(":submitters") if isinstance(sub_data, dict) else None
                ) or []
                if submitters:
                    email = (submitters[0].get(":email") or "").strip().lower()
                    if email:
                        email_stem_map[email] = stem_only

            results.append(
                _result_entry(
                    student_name,
                    safe_name,
                    status,
                    msg,
                )
            )

        # Persist email→stem map for autograder ZIP
        import json

        map_path = out_dir.parent / "email_stem_map.json"
        with open(map_path, "w", encoding="utf-8") as f:
            json.dump(email_stem_map, f, indent=2)

        return results
    finally:
        if extract_root is not None:
            shutil.rmtree(extract_root, ignore_errors=True)


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
        default=None,
        help="Optional assignment config.yaml to derive submissions_dir",
    )
    args = parser.parse_args()

    # Override out_dir from config if available
    out_dir = args.out
    if args.config is not None and args.config.exists():
        cfg = _load_app_config(args.config)
        if cfg.submissions_dir:
            out_dir = Path(cfg.submissions_dir)

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
