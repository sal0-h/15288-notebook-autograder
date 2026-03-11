"""Export graded results to Gradescope autograder JSON and Excel."""

import json
from pathlib import Path

import pandas as pd

from parse_notebook import _sort_key_qid
from utils import load_config


def export_all(config: dict) -> dict:
    """
    Read graded_results.json and write:
    - output/gradescope/{StudentName}.json (Gradescope autograder format)
    - output/Final_Grades.xlsx (human-readable Excel)

    Returns summary dict with paths and counts.
    """
    output_dir = Path(config.get("output_dir", "output"))
    graded_path = output_dir / "graded_results.json"
    gradescope_dir = output_dir / "gradescope"

    if not graded_path.exists():
        raise FileNotFoundError(f"Graded results not found: {graded_path}")

    results = json.loads(graded_path.read_text(encoding="utf-8"))
    if not results:
        return {"students": 0, "gradescope_dir": str(gradescope_dir), "excel_path": ""}

    gradescope_dir.mkdir(parents=True, exist_ok=True)

    # Collect all question IDs for Excel columns
    all_qids: set[str] = set()
    for r in results:
        all_qids.update(r.get("questions", {}).keys())
    q_cols = sorted(all_qids, key=_sort_key_qid)

    # Export Gradescope JSON per student
    for r in results:
        student_name = r.get("student_name", "Unknown")
        questions = r.get("questions", {})

        tests = []
        for qid in q_cols:
            q_data = questions.get(qid, {"score": 0, "max": 0, "feedback": ""})
            tests.append({
                "name": f"Q{qid}",
                "score": q_data.get("score", 0),
                "max_score": q_data.get("max", 0),
                "output": q_data.get("feedback", ""),
                "visibility": "visible",
            })

        gs_data = {"tests": tests}
        safe_name = "".join(c for c in student_name if c not in '/\\:*?"<>|') or "unknown_student"
        gs_path = gradescope_dir / f"{safe_name}.json"
        gs_path.write_text(json.dumps(gs_data, indent=2), encoding="utf-8")

    # Export Excel
    rows = []
    for r in results:
        row = {
            "student_name": r.get("student_name", ""),
            "total_score": r.get("total_score", 0),
        }
        for qid in q_cols:
            q_data = r.get("questions", {}).get(qid, {})
            row[f"Q{qid}"] = q_data.get("score", "")
        row["summary_feedback"] = r.get("summary_feedback", "")
        rows.append(row)

    cols = ["student_name", "total_score"] + [f"Q{q}" for q in q_cols] + ["summary_feedback"]
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols]

    excel_path = output_dir / "Final_Grades.xlsx"
    df.to_excel(excel_path, index=False)

    return {
        "students": len(results),
        "gradescope_dir": str(gradescope_dir),
        "gradescope_files": len(results),
        "excel_path": str(excel_path),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()

    config = load_config(args.config)
    summary = export_all(config)
    print(f"Exported {summary['students']} students")
    print(f"Gradescope JSONs: {summary['gradescope_dir']}")
    print(f"Excel: {summary['excel_path']}")


if __name__ == "__main__":
    main()
