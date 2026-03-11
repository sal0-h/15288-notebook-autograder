"""Estimate token usage and cost for LLM operations."""

import json
from pathlib import Path

from grade import (
    MODEL_PRICING,
    build_group_prompt,
    estimate_tokens,
)
from rubric import _build_group_prompt
from utils import load_config, DEFAULT_MODEL

RUBRIC_SYSTEM_LEN = 800  # approx chars
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


def estimate_rubrics(config: dict) -> dict:
    """Estimate tokens and cost for rubric generation."""
    output_dir = Path(config.get("output_dir", "output"))
    solution_path = output_dir / "solution_parsed.json"
    if not solution_path.exists():
        return {"error": "Run parse first", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0}

    solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
    grading_config = config.get("grading", {})
    groups = grading_config.get("question_groups", [])
    grade_only = grading_config.get("grade_only")
    if grade_only:
        s = set(grade_only)
        groups = [[q for q in g if q in s] for g in groups]
        groups = [g for g in groups if g]
    model = config.get("rubric_model") or config.get("model") or DEFAULT_MODEL

    prompt_tokens = estimate_tokens(" " * RUBRIC_SYSTEM_LEN, 0, model)
    completion_tokens = 0
    for group in groups:
        if not group:
            continue
        text = _build_group_prompt(group, solution_parsed)
        prompt_tokens += estimate_tokens(text, 0, model)
        completion_tokens += OUTPUT_TOKENS_PER_GROUP

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cost_usd": round(_cost(prompt_tokens, completion_tokens, model), 4),
        "model": model,
        "num_groups": len([g for g in groups if g]),
    }


def estimate_grade(config: dict, student_name: str | None = None) -> dict:
    """Estimate tokens and cost for grading. If student_name is None, estimates for all students."""
    output_dir = Path(config.get("output_dir", "output"))
    parsed_dir = Path(config.get("parsed_dir", output_dir / "parsed"))
    solution_path = output_dir / "solution_parsed.json"
    if not solution_path.exists():
        return {"error": "Run parse first", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0}
    if not parsed_dir.exists():
        return {"error": "No parsed files. Run parse first.", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0}

    solution_parsed = json.loads(solution_path.read_text(encoding="utf-8"))
    grading_config = config.get("grading", {})
    groups = grading_config.get("question_groups", [])
    grade_only = grading_config.get("grade_only")
    if grade_only:
        s = set(grade_only)
        groups = [[q for q in g if q in s] for g in groups]
        groups = [g for g in groups if g]

    student_files = sorted(parsed_dir.glob("*.json"))
    if student_name:
        student_files = [p for p in student_files if p.stem == student_name]
    if not student_files:
        return {"error": "No students to grade", "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0}

    model = config.get("model") or DEFAULT_MODEL
    system_prompt = config.get("prompts", {}).get("system", "Grade.")
    max_prompt_tokens = config.get("max_prompt_tokens", 80_000)
    rubrics = config.get("rubrics", {})

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
                group, solution_parsed, sample, system_prompt, max_prompt_tokens, model, rubrics=rubrics
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
