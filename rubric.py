"""LLM-based rubric generation from solution notebook."""

import json
import logging
import re
from pathlib import Path

from openai import OpenAI

from grade import get_question_data, parse_llm_json, truncate_output
from utils import get_openai_client, load_config

logger = logging.getLogger(__name__)

RUBRIC_SYSTEM_PROMPT = """You are an expert instructor creating grading rubrics for student lab work.

Given a question and its reference solution, produce concise grading criteria for that question.
Return valid JSON only, no prose outside JSON.

For each question ID, output:
{
  "QID": {
    "points": N,
    "criteria": "Full marks: [what earns full points]. Partial credit: [common scenarios with point deductions, e.g. -1 for X, -2 for Y]. Zero: [what earns 0 points]."
  }
}

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


def generate_rubrics(config: dict, client: OpenAI | None = None) -> dict[str, dict[str, int | str]]:
    """
    Generate grading rubrics from solution_parsed.json using the LLM.

    Uses question_groups from config. One LLM call per group.
    Returns a dict: { "qid": {"points": N, "criteria": "..."}, ... }
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
    model = config.get("model", "gpt-4o")

    # When grade_only is set, only generate rubrics for those questions
    if grade_only is not None:
        grade_only_set = set(grade_only)
        groups = [[q for q in group if q in grade_only_set] for group in groups]
        groups = [g for g in groups if g]  # drop empty groups

    rubrics: dict[str, dict] = {}

    for group in groups:
        if not group:
            continue

        user_content = _build_group_prompt(group, solution_parsed)
        messages = [
            {"role": "system", "content": RUBRIC_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        max_completion_tokens = config.get("max_completion_tokens", 4096)
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=1,
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
                    pts = v.get("points")
                    criteria = v.get("criteria", "")
                    if pts is not None:
                        rubrics[normalized] = {
                            "points": int(pts) if isinstance(pts, (int, float)) else 0,
                            "criteria": str(criteria) if criteria else "",
                        }
                    else:
                        sol_q = get_question_data(solution_parsed, normalized)
                        pts_fallback = (sol_q or {}).get("points", 0)
                        rubrics[normalized] = {
                            "points": pts_fallback,
                            "criteria": str(criteria) if criteria else "",
                        }
        except Exception as e:
            logger.exception("Rubric generation failed for group %s: %s", group, e)
            for qid in group:
                sol_q = get_question_data(solution_parsed, qid)
                pts = (sol_q or {}).get("points", 0)
                rubrics[qid] = {"points": pts, "criteria": f"[generation failed: {e}]"}

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
