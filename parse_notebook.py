"""Config-driven notebook parser for solution and student notebooks."""

import argparse
import json
import logging
import re
from pathlib import Path

from utils import load_config

logger = logging.getLogger(__name__)


def md_text(cell: dict) -> str:
    """Extract markdown/text from a cell's source."""
    return "".join(cell.get("source", []))


def extract_code_outputs(
    code_cell: dict,
    keep_images_base64: bool = True,
) -> dict:
    """Extract code, output text, and images from a code cell."""
    code = "".join(code_cell.get("source", []))
    out_text: list[str] = []
    images: list[dict] = []

    for out in code_cell.get("outputs", []):
        ot = out.get("output_type")

        if ot == "stream":
            out_text.append("".join(out.get("text", [])))

        elif ot in ("execute_result", "display_data"):
            data = out.get("data", {})
            if "text/plain" in data:
                out_text.append("".join(data.get("text/plain", [])))

            if keep_images_base64:
                if "image/png" in data:
                    images.append({"mime": "image/png", "base64": data["image/png"]})
                if "image/jpeg" in data:
                    images.append({"mime": "image/jpeg", "base64": data["image/jpeg"]})

        elif ot == "error":
            out_text.append("".join(out.get("traceback", [])))

    return {
        "code": code,
        "output_text": "".join(out_text).strip(),
        "images": images,
    }


def parse_notebook(nb_path: Path, config: dict) -> dict:
    """
    Parse a Jupyter notebook into structured sections and questions.

    Captures per-question: code cells, text outputs, markdown answer cells, and base64 images.
    """
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    cells = nb.get("cells", [])

    parsing = config.get("parsing", {})
    section_re = re.compile(
        parsing.get("section_regex", r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b"),
        re.IGNORECASE,
    )
    question_re = re.compile(
        parsing.get("question_regex", r"(?i)^\s*-\s*Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]"),
        re.MULTILINE,
    )
    keep_images = parsing.get("keep_images", True)

    def is_section(cell: dict) -> bool:
        return cell.get("cell_type") == "markdown" and section_re.search(md_text(cell)) is not None

    def match_question(cell: dict) -> re.Match | None:
        if cell.get("cell_type") != "markdown":
            return None
        return question_re.search(md_text(cell))

    result: dict = {"sections": {}, "source_file": str(nb_path)}
    current_section: str | None = None
    seen_qids: dict[str, int] = {}  # qid -> cell index (for duplicate detection)

    i = 0
    while i < len(cells):
        cell = cells[i]

        if is_section(cell):
            md = md_text(cell)
            sec_id = section_re.search(md).group(1)
            current_section = sec_id
            result["sections"].setdefault(sec_id, {"overview_markdown": "", "questions": {}})
            result["sections"][sec_id]["overview_markdown"] = md
            i += 1
            continue

        qm = match_question(cell)
        if qm:
            sec_id, qnum, pts = qm.group(1), qm.group(2), int(qm.group(3))
            qid = f"{sec_id}.{qnum}"

            if current_section != sec_id:
                current_section = sec_id
                result["sections"].setdefault(sec_id, {"overview_markdown": "", "questions": {}})

            q_md = md_text(cell)

            q_obj: dict = {
                "points": pts,
                "question_markdown": q_md,
                "answer_cells": [],
                "answer_code_concat": "",
                "answer_text_concat": "",
                "answer_markdown_concat": "",
            }

            code_parts: list[str] = []
            text_parts: list[str] = []
            markdown_parts: list[str] = []

            j = i + 1
            while j < len(cells):
                nxt = cells[j]

                if is_section(nxt) or match_question(nxt):
                    break

                if nxt.get("cell_type") == "code":
                    artifacts = extract_code_outputs(nxt, keep_images_base64=keep_images)
                    artifacts["cell_index"] = j
                    q_obj["answer_cells"].append(artifacts)

                    if artifacts["code"].strip():
                        code_parts.append(artifacts["code"].rstrip())
                    if artifacts["output_text"].strip():
                        text_parts.append(artifacts["output_text"].rstrip())

                elif nxt.get("cell_type") == "markdown":
                    md_content = md_text(nxt).strip()
                    if md_content:
                        markdown_parts.append(md_content)

                j += 1

            q_obj["answer_code_concat"] = "\n\n".join(code_parts).strip()
            q_obj["answer_text_concat"] = "\n\n".join(text_parts).strip()
            q_obj["answer_markdown_concat"] = "\n\n".join(markdown_parts).strip()

            if qid in seen_qids:
                dupes = result.setdefault("duplicate_qids", [])
                if qid not in dupes:
                    dupes.append(qid)
            seen_qids[qid] = i

            result["sections"][sec_id]["questions"][qid] = q_obj
            i = j
            continue

        i += 1

    return result


def _sort_key_qid(qid: str) -> tuple[int, int] | tuple[float, str]:
    """Sort key for question IDs. Standard X.Y format sorts numerically; others fall back to string."""
    parts = qid.split(".")
    if len(parts) == 2:
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    return (999_999, qid)  # non-standard IDs at end


def get_all_question_ids(parsed: dict) -> list[str]:
    """Return sorted list of all question IDs (e.g. ['1.1', '1.2', '4.1'])."""
    ids: list[str] = []
    for sec_data in parsed.get("sections", {}).values():
        ids.extend(sec_data.get("questions", {}).keys())
    return sorted(ids, key=_sort_key_qid)


def get_total_points(parsed: dict) -> int | float:
    """Sum of max points across all questions."""
    total = 0
    for sec_data in parsed.get("sections", {}).values():
        for q in sec_data.get("questions", {}).values():
            total += q.get("points", 0)
    return total


def parse_all_students(config: dict) -> tuple[dict | None, list[dict]]:
    """
    Parse solution notebook and all student notebooks.

    Returns:
        (solution_parsed, verification_report)
        solution_parsed is None if solution notebook not found.
        verification_report is a list of per-student dicts with status, questions_found, etc.
    """
    submissions_dir = Path(config.get("submissions_dir", "output/submissions"))
    parsed_dir = Path(config.get("parsed_dir", "output/parsed"))
    solution_notebook = Path(config.get("solution_notebook", ""))

    parsed_dir.mkdir(parents=True, exist_ok=True)

    solution_parsed: dict | None = None
    solution_question_ids: list[str] = []
    solution_total_pts = 0

    if solution_notebook and Path(solution_notebook).exists():
        solution_parsed = parse_notebook(Path(solution_notebook), config)
        solution_question_ids = get_all_question_ids(solution_parsed)
        solution_total_pts = get_total_points(solution_parsed)

        out_path = Path(config.get("output_dir", "output")) / "solution_parsed.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(solution_parsed, indent=2), encoding="utf-8")

    report: list[dict] = []
    student_files = list(submissions_dir.glob("*.ipynb")) if submissions_dir.exists() else []

    for nb_path in student_files:
        student_name = nb_path.stem
        try:
            parsed = parse_notebook(nb_path, config)
        except Exception as e:
            logger.warning("Parse failed for %s: %s", student_name, e)
            report.append({
                "student_name": student_name,
                "status": "error",
                "questions_found": [],
                "questions_missing": solution_question_ids,
                "total_points_possible": solution_total_pts,
                "message": str(e),
            })
            continue

        parsed["student_name"] = student_name
        out_path = parsed_dir / f"{student_name}.json"
        out_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")

        found = get_all_question_ids(parsed)
        missing = [q for q in solution_question_ids if q not in found]
        status = "ok" if not missing else "warning"

        report.append({
            "student_name": student_name,
            "status": status,
            "questions_found": found,
            "questions_missing": missing,
            "total_points_possible": get_total_points(parsed),
            "message": "",
        })

    return solution_parsed, report


def main():
    parser = argparse.ArgumentParser(description="Parse solution and student notebooks")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()

    config = load_config(args.config)
    solution_parsed, report = parse_all_students(config)

    if solution_parsed:
        qids = get_all_question_ids(solution_parsed)
        print(f"Solution: {len(qids)} questions, {get_total_points(solution_parsed)} pts")
    else:
        print("Solution notebook not found or not parsed.")

    for r in report:
        icon = "✓" if r["status"] == "ok" else "⚠"
        print(f"{icon} {r['student_name']}: {len(r['questions_found'])} questions, missing {r['questions_missing']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
