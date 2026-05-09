"""Config-driven notebook parser for solution and student notebooks.

Typical ``.ipynb`` file (JSON, nbformat v4): a top-level object with ``nbformat``,
``metadata``, and ``cells`` — an **ordered list** of cells. Each cell is a dict with:

- ``cell_type``: ``"markdown"`` or ``"code"`` (this module only uses those).
- ``source``: list of string fragments (often one per line); join them for full text.
- ``outputs``: only on code cells; list of output objects (``stream``, ``execute_result``,
  ``display_data``, ``error``, etc.) with text under ``text`` or ``data["text/plain"]``,
  and plot bytes under ``data["image/png"]`` / ``image/jpeg``.

This code reads JSON with :func:`json.loads` and walks ``cells`` linearly; it does not
use the ``nbformat`` package.

Notebook parsing contract (regex and scan)
------------------------------------------

**Structural cells**

Only **markdown** cells are considered for section and question detection. Code cells
never match ``section_regex`` or ``question_regex``. Other cell types (e.g. raw) are
ignored for structure but are still skipped over while scanning indices.

**Scan order**

Cells are visited in list order. For each cell:

1. If it matches ``section_regex``, it opens/updates that section (see below). The same
   markdown cell may **also** match ``question_regex`` (e.g. CMU handouts with a section
   title and a ``- 1 <font>… [pts]`` prompt in one cell); in that case **both** are
   applied: section first, then the question starting in that cell.
2. Else if it matches ``question_regex``, it starts a question; following cells are
   attached as answers until a cell matches either regex again.

**Repeated section numbers**

If ``section_regex`` matches the same section id again (e.g. a second ``# <font>2 …`` block),
the parser assigns a **numeric** disambiguated section id ``{N*1000 + occurrence}`` for digit
sections (e.g. second block for section ``2`` becomes ``2002``) so question IDs stay valid for
``normalize_qid`` (only ``\\d+.\\d+``). The first block keeps the original id (``2``).

**Repeated dash index (same section)**

If a 2-group ``question_regex`` would create a QID that already exists in that section
(e.g. two ``- 1`` lines under the same section), the parser assigns the next free integer
suffix in that section (``6.1`` then ``6.2``) so structure remains machine-readable.

**``section_regex``** (compiled with ``re.IGNORECASE``)

- Applied to the full markdown cell text via :func:`re.search` (first match anywhere
  in the cell).
- The pattern **must** define **capture group 1** as the section id string (e.g.
  ``"1"``, ``"2"``) used as keys under ``result["sections"]`` and to build QIDs with
  question captures.
- If the same section id appears in multiple section-header cells, later cells
  overwrite ``overview_markdown`` for that section.
- **Subsection headings** that reuse ``# <font…>`` with a decimal (e.g. ``1.1 Read…``)
  can be mistaken for a new section ``1`` if the regex only captures a leading digit
  (``(\\d+)\\b`` matches ``1`` in ``1.1``). Prefer a negative lookahead such as
  ``(\\d+)(?!\\.)`` when main sections are written ``1 Title`` / ``2 Title`` but
  subsections are ``1.1 Subtitle``.

**``question_regex``** (compiled with ``re.MULTILINE``)

- Applied to the full markdown cell text via :func:`re.search` (first match anywhere
  in the cell; ``^`` in the pattern matches after newlines inside the cell).
- The pattern **must** expose captures in one of these shapes (see
  :func:`parse_notebook`):

  - **Four groups:** ``(-\\s*)?``, section id, question number, points — groups
    ``2``, ``3``, ``4`` are used (group ``1`` is optional dash/prefix).
  - **Three groups:** section id, question number, points — groups ``1``, ``2``, ``3``.
  - **Two groups:** per-section prompt index, points — groups ``1``, ``2``. The
    question id is ``"{current_section}.{group1}"`` where ``current_section`` is the
    section id from the most recent ``section_regex`` match. If no section has been
    opened yet, the parser uses **implicit section ``1``** (and logs at INFO).

- Points are parsed with :class:`int` (must be numeric in the match).

**Answer attachment**

After a question header cell, every following **code** or **markdown** cell is part
of that question's answer until the next markdown cell that matches ``section_regex``
or ``question_regex``. Empty markdown (after strip) is skipped without breaking the
run. If ``section_regex`` is too broad, it can match student headings (e.g.
``### 1. …``) and **truncate answers early**; tighten the pattern to real handout
headers (distinctive HTML, wording, or ``#`` level).

"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from config_models import AppConfig, sort_key_qid
from config_models import load_app_config

logger = logging.getLogger(__name__)

VALID_QUESTION_TYPES = {"code", "plot", "analysis", "open-ended", "exact", "mixed"}


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


def _collect_answer_cells(
    cells: list[dict],
    start: int,
    match_section,
    match_question,
    keep_images: bool,
) -> tuple[dict, int]:
    """Collect answer cells for a question starting at index `start`.

    Iterates through cells following a question header, collecting code and markdown
    cells until hitting another section/question header. Extracts code, text outputs,
    and markdown content.

    Returns:
        (question_data_updates, next_index) where question_data_updates contains:
        - answer_code_concat: joined non-empty code cells
        - answer_text_concat: joined non-empty output text
        - answer_markdown_concat: joined non-empty markdown cells
        - answer_cells: list of extracted cell artifacts
        next_index is the position to resume scanning after this question's answers
    """
    answer_cells: list[dict] = []
    code_parts: list[str] = []
    text_parts: list[str] = []
    markdown_parts: list[str] = []

    j = start + 1
    while j < len(cells):
        nxt = cells[j]

        if match_section(nxt) or match_question(nxt):
            break

        if nxt.get("cell_type") == "code":
            artifacts = extract_code_outputs(nxt, keep_images_base64=keep_images)
            answer_cells.append(artifacts)

            if artifacts["code"].strip():
                code_parts.append(artifacts["code"].rstrip())
            if artifacts["output_text"].strip():
                text_parts.append(artifacts["output_text"].rstrip())

        elif nxt.get("cell_type") == "markdown":
            md_content = md_text(nxt).strip()
            if md_content:
                markdown_parts.append(md_content)

        j += 1

    return (
        {
            "answer_cells": answer_cells,
            "answer_code_concat": "\n\n".join(code_parts).strip(),
            "answer_text_concat": "\n\n".join(text_parts).strip(),
            "answer_markdown_concat": "\n\n".join(markdown_parts).strip(),
        },
        j,
    )


def parse_notebook(nb_path: Path, config: AppConfig) -> dict:
    """Parse a Jupyter notebook into structured sections and questions.

    Regex rules, compile flags, capture groups, and scan order are documented in the
    module docstring ("Notebook parsing contract").

    Per question, collects: code cells, stdout/plain outputs, markdown answer cells,
    and optional base64 images (when ``parsing.keep_images`` is true).
    """
    nb = json.loads(nb_path.read_text(encoding="utf-8"))
    cells = nb.get("cells", [])

    parsing = config.parsing
    section_re = re.compile(parsing.section_regex, re.IGNORECASE)
    question_re = re.compile(parsing.question_regex, re.MULTILINE)
    keep_images = parsing.keep_images

    def match_section(cell: dict) -> re.Match | None:
        """Return the section regex match if this markdown cell opens a section."""
        if cell.get("cell_type") != "markdown":
            return None
        return section_re.search(md_text(cell))

    def match_question(cell: dict) -> re.Match | None:
        if cell.get("cell_type") != "markdown":
            return None
        return question_re.search(md_text(cell))

    def ensure_section(sec_id: str) -> None:
        result["sections"].setdefault(
            sec_id, {"overview_markdown": "", "questions": {}}
        )

    result: dict = {"sections": {}, "source_file": str(nb_path)}
    current_section: str | None = None
    seen_qids: set[str] = set()
    section_occurrence: dict[str, int] = {}

    i = 0
    while i < len(cells):
        cell = cells[i]

        sm = match_section(cell) if cell.get("cell_type") == "markdown" else None

        if sm:
            raw_sec = sm.group(1)
            section_occurrence[raw_sec] = section_occurrence.get(raw_sec, 0) + 1
            occ = section_occurrence[raw_sec]
            if occ == 1:
                sec_id = raw_sec
            elif raw_sec.isdigit():
                sec_id = str(int(raw_sec) * 1000 + occ)
            else:
                sec_id = f"{raw_sec}{occ}"
            current_section = sec_id
            ensure_section(sec_id)
            result["sections"][sec_id]["overview_markdown"] = md_text(cell)

        qm = match_question(cell) if cell.get("cell_type") == "markdown" else None
        if qm:
            # Regex capture shapes:
            # - 4 groups: (optional_dash, section, qnum, pts)
            # - 3 groups: (section, qnum, pts)
            # - 2 groups: (dash_index_within_section, pts) → qid uses current_section
            if qm.lastindex is not None and qm.lastindex >= 4:
                sec_id, qnum, pts = qm.group(2), qm.group(3), int(qm.group(4))
            elif qm.lastindex == 3:
                sec_id, qnum, pts = qm.group(1), qm.group(2), int(qm.group(3))
            elif qm.lastindex == 2:
                if current_section is None:
                    sec_id = "1"
                    current_section = "1"
                    ensure_section(sec_id)
                    logger.info(
                        "Question before first section in %s — using implicit section 1",
                        nb_path.name,
                    )
                else:
                    sec_id = current_section
                qnum = qm.group(1)
                pts = int(qm.group(2))
            else:
                logger.warning(
                    "question_regex in %s produced unexpected capture count "
                    "(lastindex=%s) — skipping cell",
                    nb_path.name,
                    qm.lastindex,
                )
                i += 1
                continue
            qid = f"{sec_id}.{qnum}"
            ensure_section(sec_id)
            # CMU dash-font handouts repeat "- 1" under one section; bump only for 2-group regex.
            if qm.lastindex == 2 and qid in result["sections"][sec_id]["questions"]:
                n = int(qnum) if str(qnum).isdigit() else 1
                while f"{sec_id}.{n}" in result["sections"][sec_id]["questions"]:
                    n += 1
                qid = f"{sec_id}.{n}"

            if current_section != sec_id:
                logger.info(
                    "Question %s implies section %s (previous was %s) — creating implicitly",
                    qid,
                    sec_id,
                    current_section,
                )
                current_section = sec_id
                ensure_section(sec_id)

            # Extract question type from cell metadata tags (e.g., ["type:analysis"])
            cell_tags = cell.get("metadata", {}).get("tags") or []
            question_type = "mixed"
            for tag in cell_tags if isinstance(cell_tags, list) else []:
                if isinstance(tag, str) and tag.startswith("type:"):
                    candidate = tag[5:].strip().lower()
                    if candidate in VALID_QUESTION_TYPES:
                        question_type = candidate
                        break

            q_obj: dict = {
                "points": pts,
                "question_markdown": md_text(cell),
                "question_type": question_type,
            }

            # Collect answer cells and concatenations
            answer_data, next_i = _collect_answer_cells(
                cells, i, match_section, match_question, keep_images
            )
            q_obj.update(answer_data)

            if qid in seen_qids:
                dupes = result.setdefault("duplicate_qids", [])
                if qid not in dupes:
                    dupes.append(qid)
                logger.warning(
                    "Duplicate question ID %s in %s — last occurrence wins",
                    qid,
                    nb_path.name,
                )
            seen_qids.add(qid)

            result["sections"][sec_id]["questions"][qid] = q_obj
            i = next_i
            continue

        i += 1

    return result


def get_all_question_ids(parsed: dict) -> list[str]:
    """Return sorted list of all question IDs (e.g. ['1.1', '1.2', '4.1'])."""
    ids: list[str] = []
    for sec_data in parsed.get("sections", {}).values():
        ids.extend(sec_data.get("questions", {}).keys())
    return sorted(ids, key=sort_key_qid)


def get_total_points(parsed: dict) -> int | float:
    """Sum of max points across all questions."""
    total = 0
    for sec_data in parsed.get("sections", {}).values():
        for q in sec_data.get("questions", {}).values():
            total += q.get("points", 0)
    return total


def parse_all_students(config: AppConfig) -> tuple[dict | None, list[dict]]:
    """
    Parse solution notebook and all student notebooks.

    Returns:
        (solution_parsed, verification_report)
        solution_parsed is None if solution notebook not found.
        verification_report is a list of per-student dicts with status, questions_found, etc.
    """
    from config_models import get_assignment_output_paths
    from utils import get_job_logger

    logger = get_job_logger(config, __name__)

    paths = get_assignment_output_paths(config)
    submissions_dir = Path(config.submissions_dir)
    parsed_dir = paths.parsed_dir
    solution_notebook = Path(config.solution_notebook)

    parsed_dir.mkdir(parents=True, exist_ok=True)

    solution_parsed: dict | None = None
    solution_question_ids: list[str] = []
    solution_total_pts = 0

    if solution_notebook and Path(solution_notebook).exists():
        solution_parsed = parse_notebook(Path(solution_notebook), config)
        solution_question_ids = get_all_question_ids(solution_parsed)
        solution_total_pts = get_total_points(solution_parsed)

        out_path = paths.solution_parsed
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(solution_parsed, indent=2), encoding="utf-8")

    grade_only = config.grading.get_grade_only()
    expected_question_ids: list[str] = (
        list(grade_only) if grade_only else list(solution_question_ids)
    )
    solution_id_set = set(solution_question_ids)

    report: list[dict] = []
    student_files = (
        list(submissions_dir.glob("*.ipynb")) if submissions_dir.exists() else []
    )

    for nb_path in student_files:
        student_name = nb_path.stem
        try:
            parsed = parse_notebook(nb_path, config)
        except (json.JSONDecodeError, KeyError, ValueError, OSError) as e:
            logger.warning("Parse failed for %s: %s", student_name, e)
            report.append(
                {
                    "student_name": student_name,
                    "status": "error",
                    "questions_found": [],
                    "questions_missing": list(expected_question_ids),
                    "questions_unexpected": [],
                    "questions_duplicate": [],
                    "questions_matched_count": 0,
                    "questions_expected_count": len(expected_question_ids),
                    "total_points_possible": solution_total_pts,
                    "message": str(e),
                }
            )
            continue

        parsed["student_name"] = student_name
        out_path = parsed_dir / f"{student_name}.json"
        out_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")

        found = get_all_question_ids(parsed)
        found_set = set(found)
        missing = [q for q in expected_question_ids if q not in found_set]
        unexpected = [q for q in found if q not in solution_id_set]
        duplicate = parsed.get("duplicate_qids", [])
        matched_count = sum(1 for q in expected_question_ids if q in found_set)
        status = "ok" if not missing and not unexpected and not duplicate else "warning"

        report.append(
            {
                "student_name": student_name,
                "status": status,
                "questions_found": found,
                "questions_missing": missing,
                "questions_unexpected": unexpected,
                "questions_duplicate": duplicate,
                "questions_matched_count": matched_count,
                "questions_expected_count": len(expected_question_ids),
                "total_points_possible": get_total_points(parsed),
                "message": "",
            }
        )

    return solution_parsed, report


def main():
    parser = argparse.ArgumentParser(description="Parse solution and student notebooks")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to assignment config.yaml (required, typically output/{assignment_name}/config.yaml)",
    )
    args = parser.parse_args()
    if args.config is None:
        parser.error(
            "--config is required and must point to output/{assignment_name}/config.yaml"
        )

    config = load_app_config(args.config)
    solution_parsed, report = parse_all_students(config)

    if solution_parsed:
        qids = get_all_question_ids(solution_parsed)
        print(
            f"Solution: {len(qids)} questions, {get_total_points(solution_parsed)} pts"
        )
    else:
        print("Solution notebook not found or not parsed.")

    for r in report:
        icon = "✓" if r["status"] == "ok" else "⚠"
        print(
            f"{icon} {r['student_name']}: {len(r['questions_found'])} questions, missing {r['questions_missing']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
