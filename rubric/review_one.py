"""Single-group rubric review worker."""

import json

from grade import MAX_VALIDATION_RETRIES
from llm import complete_json_chat, retry_with_exponential_backoff
from prompt_builder import get_question_data, parse_llm_json
from rubric.jobs import RubricReviewJob
from rubric.sanitize import sanitize_llm_text
from utils import get_job_logger, get_openai_client

_MAX_RUBRIC_LLM_ATTEMPTS = MAX_VALIDATION_RETRIES + 1


def review_one_group(job: RubricReviewJob) -> dict[str, dict]:
    """Review rubrics for one group; return revised qid -> rubric dicts."""
    group = job.group
    rubrics = job.rubrics
    solution_parsed = job.solution_parsed
    config = job.config
    review_prompt = job.review_prompt
    model = job.model
    max_tokens = job.max_completion_tokens
    client = job.client
    log = get_job_logger(config, __name__)
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

    messages = [
        {"role": "system", "content": review_prompt},
        {"role": "user", "content": "\n".join(parts)},
    ]

    def _once() -> dict[str, dict]:
        completion = complete_json_chat(
            client,
            model=model,
            messages=messages,
            max_completion_tokens=max_tokens,
        )
        usage = completion.usage
        content = completion.text
        raw = parse_llm_json(content)

        revised: dict[str, dict] = {}
        for k, v in raw.items():
            normalized = k.strip().lstrip("Qq").strip()
            if normalized not in group_rubrics or not isinstance(v, dict):
                continue
            original = group_rubrics[normalized]
            v["points"] = original["points"]
            if "items" in v and "items" in original:
                orig_items = original["items"]
                new_items = v["items"]
                if len(new_items) == len(orig_items):
                    for oi, ni in zip(orig_items, new_items):
                        ni["description"] = (
                            sanitize_llm_text(str(ni.get("description", "")).strip())
                            or oi["description"]
                        )
                        ni["deduction"] = oi["deduction"]
                else:
                    v["items"] = orig_items
            pts = float(original.get("points", 0))
            items = v.get("items", [])
            if items:
                total = sum(float(i.get("deduction", 0)) for i in items)
                if abs(total - pts) > 0.01:
                    log.warning(
                        "Rubric review for Q%s: deduction sum %.2f != points %.2f after review; "
                        "reverting to original rubric",
                        normalized,
                        total,
                        pts,
                    )
                    revised[normalized] = original
                    continue
            revised[normalized] = v
        log.info(
            "Rubric review - group complete: %d/%d questions revised (tokens: %d in / %d out)",
            len(revised),
            len(group_rubrics),
            usage.prompt_tokens,
            usage.completion_tokens,
        )
        return revised

    log.info("Rubric review - group: %s (model=%s)", group, model)

    success, last_error = retry_with_exponential_backoff(
        _once,
        max_attempts=_MAX_RUBRIC_LLM_ATTEMPTS,
        on_before_retry=lambda attempt, delay: log.warning(
            "Rubric review retry %d for group %s after %ds",
            attempt,
            group,
            int(delay),
        ),
        on_attempt_failed=lambda attempt, err: log.warning(
            "Rubric review attempt %d failed for group %s: %s",
            attempt,
            group,
            err,
        ),
    )

    if success is not None:
        return success

    log.warning(
        "Rubric review failed for group %s after %d attempts: %s",
        group,
        _MAX_RUBRIC_LLM_ATTEMPTS,
        last_error,
    )
    return {}
