"""Estimate token usage and cost for LLM operations."""

import json
from pathlib import Path

from grading_models import MODEL_PRICING
from prompt_builder import build_group_prompt, estimate_tokens, load_prompt
from rubric import _build_group_prompt
from utils import (
    AppConfig,
    ensure_app_config,
    DEFAULT_MODEL,
    get_effective_question_groups,
    load_config,
)

RUBRIC_SYSTEM_LEN = 800  # approx chars
RUBRIC_REVIEW_SYSTEM_LEN = 700  # approx chars for review pass
OUTPUT_TOKENS_PER_GROUP = 500  # rubric output
OUTPUT_TOKENS_PER_GRADE_GROUP = 1200  # grading output (LLM returns JSON with feedback per question; 400 was too low)


def _cost(prompt_tokens: int, completion_tokens: int, model: str) -> float:
    inp, out = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    return (prompt_tokens / 1e6 * inp) + (completion_tokens / 1e6 * out)


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
                    if p.get("type") == "text":
                        text_len += len(p.get("text", ""))
                    elif p.get("type") == "image_url":
                        n_img += 1
            total += estimate_tokens(" " * text_len if text_len else "", n_img, model)
    return total


def estimate_rubrics(config: AppConfig | dict) -> dict:
    """Estimate tokens and cost for rubric generation."""
    cfg = ensure_app_config(config)
    output_dir = Path(cfg.output_dir)
    solution_path = output_dir / "solution_parsed.json"
    if not solution_path.exists():
        return {
            "error": "Run parse first",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0,
        }

    solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
    grading_config = cfg.grading
    groups = get_effective_question_groups(grading_config)
    model = cfg.rubric_model or cfg.model or DEFAULT_MODEL

    prompt_tokens = estimate_tokens(" " * RUBRIC_SYSTEM_LEN, 0, model)
    completion_tokens = 0
    for group in groups:
        if not group:
            continue
        text = _build_group_prompt(group, solution_parsed)
        prompt_tokens += estimate_tokens(text, 0, model)
        completion_tokens += OUTPUT_TOKENS_PER_GROUP

    # Rubric review pass (when enabled) adds one LLM call per group
    rubric_review = cfg.rubric_review
    if rubric_review:
        review_prompt = 0
        for group in groups:
            if not group:
                continue
            text = _build_group_prompt(group, solution_parsed)
            # Review input: system + question text + rubric JSON (~output size)
            review_prompt += (
                estimate_tokens(" " * RUBRIC_REVIEW_SYSTEM_LEN, 0, model)
                + estimate_tokens(text, 0, model)
                + OUTPUT_TOKENS_PER_GROUP  # rubric JSON in input
            )
            completion_tokens += OUTPUT_TOKENS_PER_GROUP
        prompt_tokens += review_prompt

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": round(_cost(prompt_tokens, completion_tokens, model), 4),
        "model": model,
        "num_groups": len([g for g in groups if g]),
        "rubric_review": rubric_review,
    }


def estimate_grade(config: AppConfig | dict, student_name: str | None = None) -> dict:
    """Estimate tokens and cost for grading. If student_name is None, estimates for all students."""
    cfg = ensure_app_config(config)
    output_dir = Path(cfg.output_dir)
    parsed_dir = Path(cfg.parsed_dir)
    solution_path = output_dir / "solution_parsed.json"
    if not solution_path.exists():
        return {
            "error": "Run parse first",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0,
        }
    if not parsed_dir.exists():
        return {
            "error": "No parsed files. Run parse first.",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0,
        }

    solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
    grading_config = cfg.grading
    groups = get_effective_question_groups(grading_config)

    student_files = sorted(parsed_dir.glob("*.json"))
    if student_name:
        student_files = [p for p in student_files if p.stem == student_name]
    if not student_files:
        return {
            "error": "No students to grade",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "cost_usd": 0,
        }

    model = cfg.model or DEFAULT_MODEL

    assignment_name = cfg.assignment_name
    system_prompt = load_prompt("grade_system", assignment_name=assignment_name)

    max_prompt_tokens = cfg.max_prompt_tokens
    rubrics = cfg.rubrics

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
                max_prompt_tokens,
                model,
                rubrics=rubrics,
            )
            tok += _tokens_from_messages(messages, model)
        prompt_tokens_one = max(prompt_tokens_one, tok)

    completion_tokens_one = sum(OUTPUT_TOKENS_PER_GRADE_GROUP for g in groups if g)

    n_students = len(student_files)
    prompt_tokens = prompt_tokens_one * n_students
    completion_tokens = completion_tokens_one * n_students

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": round(_cost(prompt_tokens, completion_tokens, model), 4),
        "model": model,
        "num_students": n_students,
        "num_groups": len([g for g in groups if g]),
    }
