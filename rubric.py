"""LLM-based rubric generation from solution notebook."""

import json
import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

from prompt_builder import get_question_data, parse_llm_json, truncate_output, load_prompt
from utils import (
    get_openai_client,
    load_config,
    temperature_for_model,
    DEFAULT_MODEL,
    filter_groups_by_grade_only,
)

logger = logging.getLogger(__name__)


def _sanitize_llm_text(text: str) -> str:
    """Normalize malformed control-char artifacts seen in some LLM outputs.

    Some responses contain sequences like "\x00d7" instead of "×" or
    stray control chars in words. We repair common patterns and drop
    non-whitespace C0 control characters.
    """
    if not text:
        return text

    # Common malformed fragments observed in regenerated rubrics
    replacements = {
        "\x00d7": "x",  # multiplication symbol artifact
        "\x00": "",  # strip stray nulls
        "\x19": "'",  # apostrophe artifact
        "\u2019": "'",  # normalize curly apostrophe to ASCII
        "\u2212": "-",  # normalize minus sign to ASCII
        "\u00d7": "x",  # normalize multiplication symbol to ASCII
        "\u2248": "~",  # normalize approximately symbol to ASCII
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    # Drop remaining control chars except whitespace controls
    text = "".join(c for c in text if ord(c) >= 32 or c in "\n\r\t")
    return text




def _get_rubric_generation_prompt(config: dict) -> str:
    assignment_name = config.get("assignment_name")
    return load_prompt("rubric_system", assignment_name=assignment_name)


def _get_rubric_review_prompt(config: dict) -> str:
    assignment_name = config.get("assignment_name")
    return load_prompt("review_system", assignment_name=assignment_name)


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
    (
        idx,
        group,
        solution_parsed,
        config,
        rubric_prompt,
        model,
        max_completion_tokens,
        client,
    ) = args
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
                    parsed_items.append(
                        {
                            "description": _sanitize_llm_text(
                                str(item.get("description", "")).strip()
                            )
                            or "[missing]",
                            "deduction": d,
                        }
                    )
                    total_deductions += d
                if parsed_items and abs(total_deductions - pts) > 0.01:
                    if total_deductions <= 0:
                        # If the model returned zero deductions, distribute evenly and correct last item.
                        even = round(float(pts) / len(parsed_items), 2)
                        for item in parsed_items:
                            item["deduction"] = even
                    else:
                        scale = pts / total_deductions
                        for item in parsed_items:
                            item["deduction"] = round(item["deduction"] * scale, 2)
                    diff = pts - sum(i["deduction"] for i in parsed_items)
                    parsed_items[-1]["deduction"] = round(
                        parsed_items[-1]["deduction"] + diff, 2
                    )
                rubrics_for_group[normalized] = {"points": pts, "items": parsed_items}
    except Exception as e:
        logger.exception("Rubric generation failed for group %s: %s", group, e)
        for qid in group:
            sol_q = get_question_data(solution_parsed, qid)
            pts = (sol_q or {}).get("points", 0)
            rubrics_for_group[qid] = {
                "points": pts,
                "items": [{"description": "[generation failed]", "deduction": pts}],
            }

    return (idx, group, rubrics_for_group)


def _review_one_group(
    args: tuple[list[str], dict, dict, str, str, int, OpenAI | None],
) -> dict[str, dict]:
    """Review rubrics for one group of questions against question text.

    Returns revised rubrics with softened wording where the criterion
    hardcoded reference-solution-specific values not required by the question.
    """
    group, rubrics, solution_parsed, review_prompt, model, max_tokens, client = args
    if client is None:
        client = get_openai_client()

    parts: list[str] = []
    group_rubrics: dict[str, dict] = {}
    for qid in group:
        if qid not in rubrics:
            continue
        sol_q = get_question_data(solution_parsed, qid)
        q_md = (sol_q or {}).get("question_markdown", f"Question {qid}")
        pts = rubrics[qid].get("points", 0)
        parts.append(f"--- QUESTION {qid} ({pts} pts) ---")
        parts.append(f"QUESTION TEXT:\n{q_md}\n")
        parts.append(f"CURRENT RUBRIC:\n{json.dumps({qid: rubrics[qid]}, indent=2)}\n")
        group_rubrics[qid] = rubrics[qid]

    if not parts:
        return {}

    try:
        temperature = temperature_for_model(model)
        messages = [
            {"role": "system", "content": review_prompt},
            {"role": "user", "content": "\n".join(parts)},
        ]
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_completion_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        raw = parse_llm_json(content)

        revised: dict[str, dict] = {}
        for k, v in raw.items():
            normalized = k.strip().lstrip("Qq").strip()
            if normalized not in group_rubrics or not isinstance(v, dict):
                continue
            original = group_rubrics[normalized]
            # Preserve original point values — don't let the review change them
            v["points"] = original["points"]
            # Preserve deduction amounts; only accept description rewrites
            if "items" in v and "items" in original:
                orig_items = original["items"]
                new_items = v["items"]
                if len(new_items) == len(orig_items):
                    for oi, ni in zip(orig_items, new_items):
                        ni["description"] = (
                            _sanitize_llm_text(str(ni.get("description", "")).strip())
                            or oi["description"]
                        )
                        ni["deduction"] = oi["deduction"]
                else:
                    # Item count changed — keep originals untouched
                    v["items"] = orig_items
            # Sanity-check: deduction sum must still equal points after review
            pts = float(original.get("points", 0))
            items = v.get("items", [])
            if items:
                total = sum(float(i.get("deduction", 0)) for i in items)
                if abs(total - pts) > 0.01:
                    logger.warning(
                        "Rubric review for Q%s: deduction sum %.2f != points %.2f after review; "
                        "reverting to original rubric",
                        normalized,
                        total,
                        pts,
                    )
                    revised[normalized] = original
                    continue
            revised[normalized] = v
        return revised
    except Exception as e:
        logger.warning("Rubric review failed for group %s: %s", group, e)
        return {}


def review_rubrics(
    rubrics: dict[str, dict],
    config: dict,
    solution_parsed: dict,
    client: OpenAI | None = None,
    groups_to_review: list[list[str]] | None = None,
) -> dict[str, dict]:
    """Optional second pass: review generated rubrics against question text.

    Softens criteria that hardcode reference-solution-specific values when
    the question text doesn't explicitly require them.
    When groups_to_review is set, only reviews those groups (for partial generation).
    """
    if client is None:
        client = get_openai_client()

    model = config.get("rubric_model") or config.get("model") or DEFAULT_MODEL
    max_tokens = config.get("max_completion_tokens", 4096)
    grading_config = config.get("grading", {})
    groups: list[list[str]] = groups_to_review or grading_config.get(
        "question_groups", []
    )
    grade_only: list[str] | None = grading_config.get("grade_only")

    if groups_to_review is None and grade_only is not None:
        groups = filter_groups_by_grade_only(groups, grade_only)

    review_prompt = _get_rubric_review_prompt(config)

    revised = dict(rubrics)  # start with copy
    for group in groups:
        result = _review_one_group(
            (
                group,
                rubrics,
                solution_parsed,
                review_prompt,
                model,
                max_tokens,
                client,
            )
        )
        revised.update(result)

    logger.info("Rubric review complete — %d questions revised", len(revised))
    return revised


def generate_rubrics(
    config: dict,
    client: OpenAI | None = None,
    progress_callback: Callable[[int, int, list, dict], None] | None = None,
    group_indices: list[int] | None = None,
) -> dict[str, dict[str, int | str]]:
    """
    Generate grading rubrics from solution_parsed.json using the LLM.

    Uses question_groups from config. One LLM call per group.
    When grade_only is set, only generates for those questions.
    When group_indices is set, only generates for those groups (0-based) and merges
    into existing rubrics (does not overwrite others).
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
    all_groups: list[list[str]] = grading_config.get("question_groups", [])
    grade_only: list[str] | None = grading_config.get("grade_only")
    model = config.get("rubric_model") or config.get("model") or DEFAULT_MODEL
    workers = config.get("workers", 1)
    max_completion_tokens = config.get("max_completion_tokens", 4096)
    rubric_prompt = _get_rubric_generation_prompt(config)

    # When group_indices: select those groups from the original grouping first.
    partial = group_indices is not None
    if group_indices is not None:
        valid = [i for i in group_indices if 0 <= i < len(all_groups)]
        if not valid:
            raise ValueError(
                f"Invalid group_indices {group_indices}. Valid range: 0–{len(all_groups) - 1}."
            )
        groups = [all_groups[i] for i in sorted(set(valid))]
    else:
        groups = list(all_groups)

    if grade_only is not None:
        groups = filter_groups_by_grade_only(groups, grade_only)

    rubrics: dict[str, dict] = dict(config.get("rubrics", {})) if partial else {}
    rubrics_lock = threading.Lock()
    total = len(groups)
    to_process = [
        (
            idx,
            group,
            solution_parsed,
            config,
            rubric_prompt,
            model,
            max_completion_tokens,
            client,
        )
        for idx, group in enumerate(groups)
        if group
    ]

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
            futures = {
                pool.submit(_generate_one_group, item): item for item in to_process
            }
            for future in as_completed(futures):
                idx, group, rubrics_for_group = future.result()
                with rubrics_lock:
                    rubrics.update(rubrics_for_group)
                    rubrics_snapshot = dict(rubrics)
                if progress_callback:
                    progress_callback(idx + 1, total, group, rubrics_snapshot)

    # Optional rubric review pass (Idea #2)
    if config.get("rubric_review", False):
        logger.info("Running rubric review pass...")
        rubrics = review_rubrics(
            rubrics,
            config,
            solution_parsed,
            client,
            groups_to_review=groups if partial else None,
        )

    return rubrics


def main():
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        description="Generate rubrics from solution notebook"
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()

    config = load_config(args.config)
    rubrics = generate_rubrics(config)
    print(json.dumps(rubrics, indent=2))


if __name__ == "__main__":
    main()
