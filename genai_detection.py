"""Optional second-pass LLM: flag possible GenAI-style answers. Does not change scores."""

import json
from dataclasses import dataclass
from typing import Any

from openai import OpenAI
from pydantic import BaseModel

from config_models import AppConfig
from grading_models import (
    NO_SUBMISSION,
    SKIP_FEEDBACKS,
    GenaiLlmResponse,
    GenaiQuestionResult,
)
from llm.json_runner import (
    MAX_JSON_LLM_ATTEMPTS,
    execute_llm_task,
    extract_llm_questions,
    run_jobs,
)
from prompt_builder import (
    _sanitize_student_text,
    get_question_data,
    load_prompt,
    truncate_output,
)
from results_models import GradedResult
from results_store import load_results, save_results
from utils import get_assignment_output_paths, get_job_logger, get_openai_client


def build_genai_detection_user_message(
    qids: list[str],
    student_parsed: dict,
    max_code_chars: int,
) -> str | None:
    """Build user message for optional GenAI suspicion pass.

    Includes question text, markdown answer, code, and code output (truncated).
    Returns ``None`` if there is no substantive content to analyze for any listed qid.
    """
    parts: list[str] = [
        "Analyze the following student answers for possible GenAI-assisted writing. "
        'Return a JSON object with a "results" array, one entry per question ID.\n'
    ]
    listed: list[str] = []
    cap = max(1024, int(max_code_chars))
    for qid in qids:
        qd = get_question_data(student_parsed, qid)
        if not qd:
            continue
        q_text = (qd.get("question_markdown") or "").strip()
        md_ans = (qd.get("answer_markdown_concat") or "").strip()
        code = (qd.get("answer_code_concat") or "").strip()
        output = (qd.get("answer_text_concat") or "").strip()
        if len(code) > cap:
            code = truncate_output(code, cap)
        if len(output) > cap // 2:
            output = truncate_output(output, cap // 2)
        if not q_text and not md_ans and not code:
            continue
        listed.append(qid)
        block = [f"--- QUESTION {qid} ---"]
        if q_text:
            block.append("Question text:\n" + _sanitize_student_text(q_text))
        if md_ans:
            block.append("Student markdown answer:\n" + _sanitize_student_text(md_ans))
        if code:
            block.append("Student code:\n" + _sanitize_student_text(code))
        if output:
            block.append("Code output:\n" + _sanitize_student_text(output))
        parts.append("\n\n".join(block))
    if not listed:
        return None
    parts.insert(1, f"Question IDs: {', '.join(listed)}\n")
    return "\n\n".join(parts)


@dataclass(frozen=True)
class _DetectionJob:
    index: int
    student_name: str
    questions: dict[str, dict[str, Any]]
    to_check: tuple[str, ...]
    messages: list[dict[str, Any]]
    model: str
    max_completion_tokens: int


def _feedback_skipped_for_detection(feedback: str) -> bool:
    fb = (feedback or "").strip()
    if fb == NO_SUBMISSION:
        return True
    if fb in SKIP_FEEDBACKS:
        return True
    return False


def _apply_detection_to_question(
    q: dict[str, Any], suspicious: bool, note: str | None
) -> None:
    """Merge detection fields only; scores and feedback are untouched."""
    q["suspicious_genai"] = bool(suspicious)
    note_s = (note or "").strip() if note is not None else ""
    if suspicious and note_s:
        q["suspicious_genai_note"] = note_s[:500]
    else:
        q.pop("suspicious_genai_note", None)


def _postprocess_genai_detection(
    parsed: BaseModel,
    *,
    expected_qids: tuple[str, ...],
) -> list[GenaiQuestionResult]:
    assert isinstance(parsed, GenaiLlmResponse)
    by_qid = extract_llm_questions(
        parsed.results,
        expected_qids=expected_qids,
        get_qid=lambda item: str(item.question_id),
    )
    return [
        item.model_copy(update={"question_id": qid}) for qid, item in by_qid.items()
    ]


def run_genai_detection(
    config: AppConfig,
    *,
    client: OpenAI | None = None,
) -> dict[str, Any]:
    """
    Load graded_results + parsed notebooks, call detector model per student,
    merge ``suspicious_genai`` / ``suspicious_genai_note`` into each question.

    Does not modify scores, max, or feedback.
    """
    log = get_job_logger(config, __name__)
    paths = get_assignment_output_paths(config)
    graded_path = paths.graded_results
    parsed_dir = paths.parsed_dir
    if not graded_path.exists():
        raise FileNotFoundError(f"No graded results at {graded_path}")

    results = load_results(graded_path)
    if not results:
        return {
            "students_processed": 0,
            "questions_flagged": 0,
            "students_skipped": 0,
            "errors": [],
        }

    model = (config.genai_detection_model or "").strip() or "gpt-4.1-mini"
    max_code = max(1024, int(config.genai_detection_max_code_chars))
    assignment_name = config.assignment_name
    max_completion_tokens = min(4096, config.max_completion_tokens)

    try:
        system_prompt = load_prompt(
            "genai_detection_system", assignment_name=assignment_name
        )
    except FileNotFoundError as e:
        raise FileNotFoundError(
            "Missing prompts/DEFAULT/genai_detection_system.md"
        ) from e

    oc = client or get_openai_client()
    workers = max(1, int(config.workers))
    errors: list[str] = []
    students_processed = 0
    questions_flagged = 0
    students_skipped = 0

    jobs: list[_DetectionJob] = []

    for i, row in enumerate(results):
        student_name = row.student_name
        if not student_name:
            continue
        questions = {
            qid: q.model_dump(mode="python") for qid, q in row.questions.items()
        }

        parsed_path = parsed_dir / f"{student_name}.json"
        if not parsed_path.exists():
            errors.append(f"No parsed file for {student_name!r}")
            students_skipped += 1
            continue

        try:
            student_parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            errors.append(f"Parse error {parsed_path}: {e}")
            students_skipped += 1
            continue

        qids = list(questions.keys())
        to_check: list[str] = []
        for qid in qids:
            qd = questions.get(qid)
            if qd is None:
                continue
            fb = qd.get("feedback", "")
            if _feedback_skipped_for_detection(str(fb)):
                continue
            to_check.append(qid)

        if not to_check:
            students_skipped += 1
            continue

        user_msg = build_genai_detection_user_message(
            to_check, student_parsed, max_code
        )
        if not user_msg:
            students_skipped += 1
            continue

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ]
        jobs.append(
            _DetectionJob(
                index=i,
                student_name=student_name,
                questions=questions,
                to_check=tuple(to_check),
                messages=messages,
                model=model,
                max_completion_tokens=max_completion_tokens,
            )
        )

    log.info(
        "GenAI detection: %d students queued, %d skipped, model=%s, workers=%d",
        len(jobs),
        students_skipped,
        model,
        workers,
    )

    def _run_job(
        job: _DetectionJob,
    ) -> tuple[int, dict[str, dict[str, Any]] | None, int, int, list[str]]:
        local_errors: list[str] = []
        log.info(
            "GenAI detection - %s: checking %d questions (model=%s)",
            job.student_name,
            len(job.to_check),
            job.model,
        )

        def _on_exhausted(
            last_err: BaseException | None,
        ) -> list[GenaiQuestionResult]:
            local_errors.append(f"{job.student_name}: {last_err}")
            log.error(
                "GenAI detection failed for %s after %d attempts: %s",
                job.student_name,
                MAX_JSON_LLM_ATTEMPTS,
                last_err,
            )
            return []

        raw, _usage = execute_llm_task(
            oc,
            model=job.model,
            messages=job.messages,
            max_completion_tokens=job.max_completion_tokens,
            response_model=GenaiLlmResponse,
            task_kind="genai_detection",
            logger=log,
            postprocess=lambda p: _postprocess_genai_detection(
                p, expected_qids=job.to_check
            ),
            fallback_factory=_on_exhausted,
            extra_log_fields={"student": str(job.student_name)},
        )

        if not raw:
            log.warning("GenAI detection - %s: no results returned", job.student_name)
            return (job.index, None, 0, 0, local_errors)

        updated = dict(job.questions)
        flagged = 0
        by_qid = {r.question_id: r for r in raw}
        for qid in job.to_check:
            qrow = updated.get(qid)
            if not isinstance(qrow, dict):
                continue
            entry = by_qid.get(qid)
            if entry is None:
                continue
            _apply_detection_to_question(
                qrow, entry.suspicious_genai, entry.note if entry.note else None
            )
            if entry.suspicious_genai:
                flagged += 1

        log.info(
            "GenAI detection - %s complete: %d/%d flagged",
            job.student_name,
            flagged,
            len(job.to_check),
        )
        return (job.index, updated, 1, flagged, local_errors)

    for idx, updated, processed, flagged, local_errors in run_jobs(
        jobs,
        _run_job,
        max_workers=workers,
    ):
        errors.extend(local_errors)
        if updated is None:
            continue
        students_processed += processed
        questions_flagged += flagged
        row = results[idx]
        results[idx] = GradedResult.model_validate(
            {**row.model_dump(mode="python"), "questions": updated}
        )

    save_results(graded_path, results)
    log.info(
        "GenAI detection complete: %d students processed, %d questions flagged, %d skipped, %d errors",
        students_processed,
        questions_flagged,
        students_skipped,
        len(errors),
    )
    return {
        "students_processed": students_processed,
        "questions_flagged": questions_flagged,
        "students_skipped": students_skipped,
        "errors": errors,
        "graded_results_path": str(graded_path),
    }
