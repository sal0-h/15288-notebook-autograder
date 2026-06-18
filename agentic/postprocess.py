"""Shared post-processing that maps raw crew JSON onto the SAME validated shapes the
simple-LLM path produces.

The crews are responsible only for producing JSON; these helpers re-apply the exact
guarantees the original code enforces (deduction rescaling, point/deduction
preservation on review, grade validation, score clamping). This keeps the
agentic and simple paths byte-compatible for downstream code (export, calibrate, UI).
"""

from __future__ import annotations

import json
import re

from config_models import RubricEntry, normalize_qid
from grading_models import QuestionGrade
from prompt_builder import get_question_data
from token_usage import TokenUsage


def parse_crew_json(text: str) -> dict:
    """Extract a JSON object from crew text output (handles markdown fences)."""
    if not text or not str(text).strip():
        return {}
    s = str(text).strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", s)
    if fence:
        s = fence.group(1).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(s[start : end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def normalize_rubric_json(
    raw: dict, group: list[str], solution_parsed: dict
) -> dict[str, RubricEntry]:
    """Normalize generated rubric JSON via RubricEntry.from_llm_output."""
    rubrics_for_group: dict[str, RubricEntry] = {}
    for k, v in (raw or {}).items():
        try:
            normalized = normalize_qid(k)
        except ValueError:
            continue
        if not isinstance(v, dict):
            continue
        pts_raw = v.get("points")
        if pts_raw is None:
            sol_q = get_question_data(solution_parsed, normalized)
            pts = int((sol_q or {}).get("points", 0))
            v = dict(v)
            v["points"] = pts
        try:
            rubrics_for_group[normalized] = RubricEntry.from_llm_output(v)
        except (ValueError, TypeError):
            continue
    return rubrics_for_group


def merge_reviewed_rubric(
    raw: dict, group_rubrics: dict[str, dict]
) -> dict[str, RubricEntry]:
    """Merge review JSON: accept ONLY description rewrites; preserve points/deductions."""
    revised: dict[str, RubricEntry] = {}
    for k, v in (raw or {}).items():
        try:
            normalized = normalize_qid(k)
        except ValueError:
            continue
        if normalized not in group_rubrics or not isinstance(v, dict):
            continue
        original = RubricEntry.model_validate(group_rubrics[normalized])
        v = dict(v)
        v["points"] = original.points

        if "items" in v and original.items:
            orig_items = original.items
            new_items = v.get("items", [])
            if isinstance(new_items, list) and len(new_items) == len(orig_items):
                merged_items = [
                    {
                        "description": (
                            str(ni.get("description", "")).strip() or oi.description
                        ),
                        "deduction": oi.deduction,
                    }
                    for ni, oi in zip(new_items, orig_items)
                ]
                v["items"] = merged_items
            else:
                v["items"] = [
                    {"description": oi.description, "deduction": oi.deduction}
                    for oi in orig_items
                ]

        try:
            entry = RubricEntry.model_validate(v)
            total = sum(i.deduction for i in entry.items)
            if entry.items and abs(total - entry.points) > 0.01:
                revised[normalized] = original
            else:
                revised[normalized] = entry
        except Exception:
            revised[normalized] = original
    return revised


def grades_from_crew_json(raw: dict, group: list[str]) -> list[QuestionGrade]:
    """Convert crew grading JSON (qid -> grade dict) into QuestionGrade list."""
    by_qid: dict[str, QuestionGrade] = {}
    data = raw or {}

    # Support {"grades": [...]} if the model returns structured-output shape
    if isinstance(data.get("grades"), list):
        for item in data["grades"]:
            if not isinstance(item, dict):
                continue
            qid_raw = item.get("question_id") or item.get("qid")
            if not qid_raw:
                continue
            try:
                qid = normalize_qid(str(qid_raw))
            except ValueError:
                continue
            if qid not in by_qid:
                by_qid[qid] = QuestionGrade.model_validate({**item, "question_id": qid})
        missing = [q for q in group if q not in by_qid]
        if missing:
            raise ValueError(f"Crew grading JSON missing questions: {missing}")
        return [by_qid[q] for q in group]

    for k, v in data.items():
        try:
            qid = normalize_qid(k)
        except ValueError:
            continue
        if not isinstance(v, dict):
            continue
        if qid not in by_qid:
            by_qid[qid] = QuestionGrade.model_validate({**v, "question_id": qid})

    missing = [q for q in group if q not in by_qid]
    if missing:
        raise ValueError(f"Crew grading JSON missing questions: {missing}")
    return [by_qid[q] for q in group]


def usage_from_crew_output(result) -> TokenUsage:
    """Map CrewOutput token_usage onto TokenUsage."""
    usage = getattr(result, "token_usage", None)
    if usage is None:
        return TokenUsage()
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if prompt is None and completion is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=int(prompt or 0),
        completion_tokens=int(completion or 0),
    )
