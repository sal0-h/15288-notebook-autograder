"""Single-group rubric LLM generation worker."""

from grade import MAX_VALIDATION_RETRIES
from llm import complete_json_chat, retry_with_exponential_backoff
from llm.types import TokenUsage
from prompt_builder import get_question_data, parse_llm_json
from rubric.jobs import GENERATION_FAILED, RubricGenJob
from rubric.prompts import build_rubric_group_prompt
from rubric.sanitize import sanitize_llm_text
from utils import get_job_logger, get_openai_client

# Match grade.grade_group: MAX_VALIDATION_RETRIES + 1 attempts (see grade.py)
_MAX_RUBRIC_LLM_ATTEMPTS = MAX_VALIDATION_RETRIES + 1


def generate_one_group(
    job: RubricGenJob,
) -> tuple[int, list[str], dict[str, dict], TokenUsage, bool]:
    """Generate rubric for one group. Returns (idx, group, rubrics_for_group, usage, had_error)."""
    idx = job.idx
    group = job.group
    solution_parsed = job.solution_parsed
    config = job.config
    rubric_prompt = job.rubric_prompt
    model = job.model
    max_completion_tokens = job.max_completion_tokens
    client = job.client
    log = get_job_logger(config, __name__)
    if client is None:
        client = get_openai_client()
    rubrics_for_group: dict[str, dict] = {}
    usage = TokenUsage()
    had_error = False

    if not group:
        return (idx, group, rubrics_for_group, usage, had_error)

    user_content = build_rubric_group_prompt(group, solution_parsed)
    messages = [
        {"role": "system", "content": rubric_prompt},
        {"role": "user", "content": user_content},
    ]

    def _once() -> tuple[TokenUsage, dict[str, dict]]:
        completion = complete_json_chat(
            client,
            model=model,
            messages=messages,
            max_completion_tokens=max_completion_tokens,
        )
        u = completion.usage
        content = completion.text
        raw = parse_llm_json(content)

        built: dict[str, dict] = {}
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
                            "description": sanitize_llm_text(
                                str(item.get("description", "")).strip()
                            )
                            or "[missing]",
                            "deduction": d,
                        }
                    )
                    total_deductions += d
                if parsed_items and abs(total_deductions - pts) > 0.01:
                    if total_deductions <= 0:
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
                built[normalized] = {"points": pts, "items": parsed_items}
        return u, built

    def _on_before_retry(attempt: int, delay: float) -> None:
        log.warning(
            "Rubric generation retry %d for group %s after %ds",
            attempt,
            group,
            int(delay),
        )

    log.info(
        "Rubric generation - group %d: %s (model=%s)",
        idx + 1,
        group,
        model,
    )

    success, last_error = retry_with_exponential_backoff(
        _once,
        max_attempts=_MAX_RUBRIC_LLM_ATTEMPTS,
        on_before_retry=_on_before_retry,
        on_attempt_failed=lambda attempt, err: log.warning(
            "Rubric generation attempt %d failed for group %s: %s",
            attempt,
            group,
            err,
        ),
    )

    if success is not None:
        usage, rubrics_for_group = success
        log.info(
            "Rubric generation - group %d complete: %d/%d questions parsed (tokens: %d in / %d out)",
            idx + 1,
            len(rubrics_for_group),
            len(group),
            usage.prompt_tokens,
            usage.completion_tokens,
        )
    else:
        had_error = True
        log.error(
            "Rubric generation failed for group %s after %d attempts: %s",
            group,
            _MAX_RUBRIC_LLM_ATTEMPTS,
            last_error,
        )
        for qid in group:
            sol_q = get_question_data(solution_parsed, qid)
            pts = (sol_q or {}).get("points", 0)
            rubrics_for_group[qid] = {
                "points": pts,
                "items": [{"description": GENERATION_FAILED, "deduction": pts}],
            }

    return (idx, group, rubrics_for_group, usage, had_error)
