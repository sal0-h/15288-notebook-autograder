"""Gradescope autograder harness: copy precomputed results.json by email lookup.

Invoked from ``run_autograder`` on Gradescope (working directory ``/autograder``).
"""

import json
import shutil
import sys
from pathlib import Path

RESULTS_DIR = Path("/autograder/results")
SOURCE_DIR = Path("/autograder/source")
PRECOMPUTED_DIR = SOURCE_DIR / "results"
METADATA_PATH = Path("/autograder/submission_metadata.json")
EMAIL_MAP_PATH = SOURCE_DIR / "email_stem_map.json"
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


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if not METADATA_PATH.is_file():
        OUT_PATH.write_text(
            json.dumps(error_response("Error: submission_metadata.json not found.")),
            encoding="utf-8",
        )
        return

    meta = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    users = meta.get("users") or []
    email = ""
    if users and isinstance(users[0], dict):
        email = (users[0].get("email") or "").strip().lower()

    if not email:
        name = users[0].get("name", "") if users else ""
        OUT_PATH.write_text(
            json.dumps(
                error_response(
                    f"No email found in submission metadata. Cannot look up results. (name={name!r})"
                )
            ),
            encoding="utf-8",
        )
        return

    if not EMAIL_MAP_PATH.is_file():
        OUT_PATH.write_text(
            json.dumps(
                error_response(
                    "email_stem_map.json not found in autograder package. "
                    "Re-run export and re-upload the autograder ZIP."
                )
            ),
            encoding="utf-8",
        )
        return

    email_map = json.loads(EMAIL_MAP_PATH.read_text(encoding="utf-8"))
    stem = email_map.get(email)
    if not stem:
        print(f"email lookup miss: {email!r} not in email_stem_map", file=sys.stderr)
        OUT_PATH.write_text(
            json.dumps(
                error_response(
                    f"No pre-computed results for {email}. "
                    "The instructor may need to re-run export and re-upload the autograder ZIP."
                )
            ),
            encoding="utf-8",
        )
        return

    result_path = PRECOMPUTED_DIR / f"{stem}.json"
    if not result_path.is_file():
        print(f"result file missing: {result_path}", file=sys.stderr)
        OUT_PATH.write_text(
            json.dumps(
                error_response(
                    f"Result file for {email} not found in package. "
                    "Re-run export and re-upload the autograder ZIP."
                )
            ),
            encoding="utf-8",
        )
        return

    shutil.copy(result_path, OUT_PATH)


if __name__ == "__main__":
    main()
