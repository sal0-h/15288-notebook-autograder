"""Estimate token usage and cost for LLM operations."""

import json
from typing import Any

from config_models import AppConfig, DEFAULT_MODEL, load_solution_parsed
from grading_helpers import effective_groups
from token_usage import TokenUsage, usage_cost_usd
from prompt_builder import build_group_prompt, estimate_tokens, load_prompt
from rubric_generate import build_rubric_group_prompt
from batch_grader import load_grade_queue
from utils import (
    get_assignment_output_paths,
)

RUBRIC_SYSTEM_LEN = 800  # approx chars
RUBRIC_REVIEW_SYSTEM_LEN = 700  # approx chars for review pass
OUTPUT_TOKENS_PER_GROUP = 500  # rubric output
OUTPUT_TOKENS_PER_GRADE_GROUP = 1200  # grading output (LLM returns JSON with feedback per question; 400 was too low)


def _estimate_error(message: str) -> dict[str, Any]:
    """Same token/cost shape as successful estimate responses."""
    u = TokenUsage()
    return {"error": message, **u.to_json_dict(), "cost_usd": 0}


def _cost(usage: TokenUsage, model: str) -> float:
    return usage_cost_usd(usage, model)


def _tokens_from_messages(messages: list, model: str) -> int:
    """Estimate tokens from API messages (text + image placeholders)."""
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += estimate_tokens(c, 0, model)
        elif isinstance(c, list):
            text_len = 0
            n_img = 0
            for p in c:
                if isinstance(p, dict):
                    if p.get("type") in ("text", "input_text"):
                        text_len += len(p.get("text", ""))
                    elif p.get("type") in ("image_url", "input_image"):
                        n_img += 1
            total += estimate_tokens(" " * text_len if text_len else "", n_img, model)
    return total


def estimate_rubrics(config: AppConfig) -> dict:
    """Estimate tokens and cost for rubric generation."""
    try:
        solution_parsed = load_solution_parsed(config)
    except FileNotFoundError:
        return _estimate_error("Run parse first")

    grading_config = config.grading
    groups = effective_groups(grading_config)
    model = config.rubric_model or config.model or DEFAULT_MODEL

    prompt_tokens = estimate_tokens(" " * RUBRIC_SYSTEM_LEN, 0, model)
    completion_tokens = 0
    for group in groups:
        if not group:
            continue
        text = build_rubric_group_prompt(group, solution_parsed)
        prompt_tokens += estimate_tokens(text, 0, model)
        completion_tokens += OUTPUT_TOKENS_PER_GROUP

    # Rubric review pass (when enabled) adds one LLM call per group
    rubric_review = config.rubric_review
    if rubric_review:
        review_prompt = 0
        for group in groups:
            if not group:
                continue
            text = build_rubric_group_prompt(group, solution_parsed)
            # Review input: system + question text + rubric JSON (~output size)
            review_prompt += (
                estimate_tokens(" " * RUBRIC_REVIEW_SYSTEM_LEN, 0, model)
                + estimate_tokens(text, 0, model)
                + OUTPUT_TOKENS_PER_GROUP  # rubric JSON in input
            )
            completion_tokens += OUTPUT_TOKENS_PER_GROUP
        prompt_tokens += review_prompt

    usage = TokenUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return {
        **usage.to_json_dict(),
        "cost_usd": round(_cost(usage, model), 4),
        "model": model,
        "num_groups": sum(1 for g in groups if g),
        "rubric_review": rubric_review,
    }


def estimate_grade(config: AppConfig, student_name: str | None = None) -> dict:
    """Estimate tokens and cost for grading. If student_name is None, estimates pending bulk grading only."""
    paths = get_assignment_output_paths(config)
    parsed_dir = paths.parsed_dir
    
    try:
        solution_parsed = load_solution_parsed(config)
    except FileNotFoundError:
        return _estimate_error("Run parse first")
    
    if not parsed_dir.exists():
        return _estimate_error("No parsed files. Run parse first.")

    grading_config = config.grading
    groups = effective_groups(grading_config)

    model = config.model or DEFAULT_MODEL

    assignment_name = config.assignment_name
    system_prompt = load_prompt("grade_system", assignment_name=assignment_name)

    max_prompt_tokens = config.max_prompt_tokens
    rubrics = config.rubrics

    if student_name:
        student_files = sorted(parsed_dir.glob("*.json"))
        student_files = [p for p in student_files if p.stem == student_name]
        if not student_files:
            return _estimate_error("No students to grade")
    else:
        try:
            gq = load_grade_queue(config)
        except FileNotFoundError as e:
            return _estimate_error(str(e))
        if not gq.student_files:
            return _estimate_error("No parsed files. Run parse first.")
        student_files = [p for _, p in gq.to_grade]
        total_parsed = len(gq.student_files)
        skipped_students = total_parsed - len(student_files)
        if not student_files:
            u = TokenUsage()
            ng = sum(1 for g in groups if g)
            return {
                **u.to_json_dict(),
                "cost_usd": 0.0,
                "model": model,
                "num_students": 0,
                "pending_students": 0,
                "total_parsed": total_parsed,
                "skipped_students": skipped_students,
                "num_groups": ng,
                "note": "No students pending grading (everyone already graded).",
            }

    # Sample up to 5 students and use max for conservative input estimate (variance in answer length/images)
    sample_count = min(5, len(student_files))
    prompt_tokens_one = 0
    for i in range(sample_count):
        sample = json.loads(student_files[i].read_text(encoding="utf-8"))
        tok = 0
        for group in groups:
            if not group:
                continue
            messages, _ = build_group_prompt(
                group,
                solution_parsed,
                sample,
                system_prompt,
                max_prompt_tokens=max_prompt_tokens,
                rubrics=rubrics,
            )
            tok += _tokens_from_messages(messages, model)
        prompt_tokens_one = max(prompt_tokens_one, tok)

    completion_tokens_one = sum(OUTPUT_TOKENS_PER_GRADE_GROUP for g in groups if g)

    n_students = len(student_files)
    prompt_tokens = prompt_tokens_one * n_students
    completion_tokens = completion_tokens_one * n_students

    usage = TokenUsage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    out: dict[str, Any] = {
        **usage.to_json_dict(),
        "cost_usd": round(_cost(usage, model), 4),
        "model": model,
        "num_students": n_students,
        "num_groups": sum(1 for g in groups if g),
    }
    if not student_name:
        out["pending_students"] = n_students
        out["total_parsed"] = total_parsed
        out["skipped_students"] = skipped_students
    return out
