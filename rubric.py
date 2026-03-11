"""LLM-based rubric generation from solution notebook."""

import json
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

from grade import get_question_data, parse_llm_json, truncate_output
from utils import get_openai_client, load_config, temperature_for_model, DEFAULT_MODEL

logger = logging.getLogger(__name__)

DEFAULT_RUBRIC_SYSTEM_PROMPT = """You are an expert instructor creating grading rubrics for student lab work.

Given a question and its reference solution, produce structured grading criteria for that question.
Return valid JSON only, no prose outside JSON.

For each question ID, output:
{
  "QID": {
    "points": N,
    "items": [
      {"description": "Criterion description", "deduction": 1.0},
      ...
    ]
  }
}

Constraints:
- The sum of all deduction values MUST equal the total points for that question exactly. This ensures a student who fails every criterion scores 0.
- Group related minor deductions into a single item rather than creating many small sub-deductions. As a guideline, use at most 1 item per point (e.g. a 1-point question gets at most 1 item; a 4-point question gets at most 4 items). For 1-point questions, prefer a single binary item (correct/incorrect) rather than fractional sub-deductions.
- Phrase each criterion as what the student must do (positive), not what causes deduction (negative). E.g. "Classifier uses weights='distance' and best_k" not "Did not set weights='distance'".
- For enumerated parameters (e.g., K values, p values, test_size), use the exact values from the reference code. Do not infer or add values. The reference code is the source of truth. Example: if the code uses `for kfold in [3, 5, 15, 20]`, the rubric must say (3, 5, 15, 20)—not (3, 5, 10, 15, 20) even if the question mentions "compare with K=10" as a baseline.
- Values that depend on dataset size (n, X.shape[0], training-set size, iterations): Do NOT hardcode the reference solution's numbers (e.g. "reporting 41789 for this dataset"). Students may have different n due to preprocessing. Use flexible wording such as "correctly computed from their n" or "accept if the formula is correct (e.g. 11*n for total iterations, n−1 for training examples per LOOCV fold)" so the grader can award points when students have different dataset sizes.

Be specific and actionable. The criteria should help another grader (or an LLM) consistently score student submissions."""


def _build_group_prompt(group: list[str], solution_parsed: dict) -> str:
    """Build the user prompt for one question group."""
    parts: list[str] = []
    max_output_chars = 2000

    for qid in group:
        sol_q = get_question_data(solution_parsed, qid)
        pts = (sol_q or {}).get("points", 0)
        q_md = (sol_q or {}).get("question_markdown", f"Question {qid}")

        block = f"--- QUESTION {qid} ({pts} pts) ---\n{q_md}\n\n"
        if sol_q:
            if sol_q.get("answer_code_concat"):
                block += f"REFERENCE CODE:\n{truncate_output(sol_q['answer_code_concat'], max_output_chars)}\n\n"
            if sol_q.get("answer_text_concat"):
                block += f"REFERENCE OUTPUT:\n{truncate_output(sol_q['answer_text_concat'], max_output_chars)}\n\n"
            if sol_q.get("answer_markdown_concat"):
                block += f"REFERENCE ANSWER:\n{truncate_output(sol_q['answer_markdown_concat'], max_output_chars)}\n\n"
        parts.append(block)

    return "\n".join(parts)


def _generate_one_group(
    args: tuple[int, list[str], dict, dict, str, str, int, OpenAI | None],
) -> tuple[int, list[str], dict[str, dict]]:
    """Generate rubric for one group. Returns (group_idx, group, rubrics_for_group)."""
    idx, group, solution_parsed, config, rubric_prompt, model, max_completion_tokens, client = args
    if client is None:
        client = get_openai_client()
    rubrics_for_group: dict[str, dict] = {}

    if not group:
        return (idx, group, rubrics_for_group)

    try:
        temperature = temperature_for_model(model)
        user_content = _build_group_prompt(group, solution_parsed)
        messages = [
            {"role": "system", "content": rubric_prompt},
            {"role": "user", "content": user_content},
        ]
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_completion_tokens=max_completion_tokens,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        raw = parse_llm_json(content)

        for k, v in raw.items():
            normalized = k.strip().lstrip("Qq").strip()
            if not normalized:
                continue
            if isinstance(v, dict):
                pts_raw = v.get("points")
                pts = int(pts_raw) if pts_raw is not None else None
                if pts is None:
                    sol_q = get_question_data(solution_parsed, normalized)
                    pts = (sol_q or {}).get("points", 0)
                raw_items = v.get("items", [])
                parsed_items = []
                total_deductions = 0.0
                for item in raw_items:
                    d = abs(float(item.get("deduction", 0)))
                    parsed_items.append({
                        "description": str(item.get("description", "")).strip() or "[missing]",
                        "deduction": d,
                    })
                    total_deductions += d
                if parsed_items and abs(total_deductions - pts) > 0.01:
                    scale = pts / total_deductions if total_deductions else 1.0
                    for item in parsed_items:
                        item["deduction"] = round(item["deduction"] * scale, 2)
                    diff = pts - sum(i["deduction"] for i in parsed_items)
                    parsed_items[-1]["deduction"] = round(parsed_items[-1]["deduction"] + diff, 2)
                rubrics_for_group[normalized] = {"points": pts, "items": parsed_items}
    except Exception as e:
        logger.exception("Rubric generation failed for group %s: %s", group, e)
        for qid in group:
            sol_q = get_question_data(solution_parsed, qid)
            pts = (sol_q or {}).get("points", 0)
            rubrics_for_group[qid] = {"points": pts, "items": [{"description": "[generation failed]", "deduction": pts}]}

    return (idx, group, rubrics_for_group)


def generate_rubrics(
    config: dict,
    client: OpenAI | None = None,
    progress_callback: Callable[[int, int, list, dict], None] | None = None,
) -> dict[str, dict[str, int | str]]:
    """
    Generate grading rubrics from solution_parsed.json using the LLM.

    Uses question_groups from config. One LLM call per group.
    When grade_only is set, only generates for those questions.
    Uses config.workers (default 1) for parallel generation.
    progress_callback(group_index, total_groups, group, rubrics_so_far) is called after each group.
    Returns a dict: { "qid": {"points": N, "items": [{"description": "...", "deduction": ...}], ...} }
    """
    if client is None:
        client = get_openai_client()

    output_dir = Path(config.get("output_dir", "output"))
    solution_path = output_dir / "solution_parsed.json"

    if not solution_path.exists():
        raise FileNotFoundError(
            f"Solution parsed not found: {solution_path}. Run the parse step first."
        )

    solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
    grading_config = config.get("grading", {})
    groups: list[list[str]] = grading_config.get("question_groups", [])
    grade_only: list[str] | None = grading_config.get("grade_only")
    model = config.get("rubric_model") or config.get("model") or DEFAULT_MODEL
    workers = config.get("workers", 1)
    max_completion_tokens = config.get("max_completion_tokens", 4096)
    rubric_prompt = config.get("prompts", {}).get("rubric_system") or DEFAULT_RUBRIC_SYSTEM_PROMPT

    if grade_only is not None:
        grade_only_set = set(grade_only)
        groups = [[q for q in group if q in grade_only_set] for group in groups]
        groups = [g for g in groups if g]

    rubrics: dict[str, dict] = {}
    rubrics_lock = threading.Lock()
    total = len(groups)
    to_process = [(idx, group, solution_parsed, config, rubric_prompt, model, max_completion_tokens, client)
                  for idx, group in enumerate(groups) if group]

    if workers <= 1 or len(to_process) <= 1:
        # Sequential
        for item in to_process:
            idx, group = item[0], item[1]
            if progress_callback:
                progress_callback(idx + 1, total, group, dict(rubrics))
            _, _, rubrics_for_group = _generate_one_group(item)
            rubrics.update(rubrics_for_group)
            if progress_callback:
                progress_callback(idx + 1, total, group, dict(rubrics))
    else:
        # Parallel
        if progress_callback:
            for idx, group in enumerate(groups):
                if group:
                    progress_callback(idx + 1, total, group, {})
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_generate_one_group, item): item for item in to_process}
            for future in as_completed(futures):
                idx, group, rubrics_for_group = future.result()
                with rubrics_lock:
                    rubrics.update(rubrics_for_group)
                    rubrics_snapshot = dict(rubrics)
                if progress_callback:
                    progress_callback(idx + 1, total, group, rubrics_snapshot)

    return rubrics


def main():
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Generate rubrics from solution notebook")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()

    config = load_config(args.config)
    rubrics = generate_rubrics(config)
    print(json.dumps(rubrics, indent=2))


if __name__ == "__main__":
    main()
