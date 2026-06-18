"""Grading crew with deterministic multimodal grounding.

The grounding step sends the exact message that prompt_builder.build_group_prompt
produces today (text + every student plot as an image part), driven by the grader
agent's persona. That step always sees all plots, so grading fidelity matches the
current single-call behavior.

For richer profiles, downstream agents reason over a TEXT digest of the evidence plus
the grounded grades (no raw pixels):

  lean     -> grounding grader only
  verified -> grounding grader -> verifier (text)
  panel    -> grounding grader -> correctness/rubric/leniency lenses -> adjudicator (text)

run_grading returns (grade_items, qid_to_max, usage, qid_injection_suspect). The caller
validates with QuestionGrade and clamps scores, exactly like the simple-LLM path.
"""

from __future__ import annotations

import json

from config_models import AppConfig, DEFAULT_MODEL
from grading_models import QuestionGrade
from prompt_builder import build_group_prompt
from token_usage import TokenUsage

from agentic.crew_types import CrewType
from agentic.llm import build_crew_llm
from agentic.loader import load_crew_config
from agentic.postprocess import (
    grades_from_crew_json,
    parse_crew_json,
    usage_from_crew_output,
)


def _persona_system_prompt(defn: dict) -> str:
    """Compose an agent definition (role/goal/backstory) into a system prompt."""
    parts = [str(defn.get("role", "")).strip()]
    if defn.get("goal"):
        parts.append("Goal: " + str(defn["goal"]).strip())
    if defn.get("backstory"):
        parts.append(str(defn["backstory"]).strip())
    return "\n\n".join(p for p in parts if p)


def _text_evidence_from_messages(messages: list) -> str:
    """Extract text parts from built grading messages (drops images)."""
    digest: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in (
                    "text",
                    "input_text",
                ):
                    digest.append(part.get("text", ""))
        elif isinstance(content, str):
            digest.append(content)
    return "\n".join(d for d in digest if d)


def _responses_messages_to_chat(messages: list) -> list:
    """Convert Responses-API message parts to chat/litellm multimodal format."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if isinstance(content, list):
            parts: list[dict] = []
            for p in content:
                if not isinstance(p, dict):
                    continue
                ptype = p.get("type")
                if ptype in ("text", "input_text"):
                    parts.append({"type": "text", "text": p.get("text", "")})
                elif ptype in ("image_url", "input_image"):
                    url = p.get("image_url")
                    if isinstance(url, dict):
                        url = url.get("url", "")
                    if url:
                        parts.append({"type": "image_url", "image_url": {"url": url}})
            out.append({"role": role, "content": parts})
        else:
            out.append({"role": role, "content": str(content or "")})
    return out


def _agent(defn: dict, llm):
    from crewai import Agent  # noqa: PLC0415

    return Agent(
        role=defn["role"],
        goal=defn["goal"],
        backstory=defn.get("backstory", ""),
        llm=llm,
        verbose=False,
        allow_delegation=False,
    )


def _task(defn: dict, agent, context: list | None = None):
    from crewai import Task  # noqa: PLC0415

    return Task(
        description=defn["description"],
        expected_output=defn["expected_output"],
        agent=agent,
        context=context or [],
    )


def _run_text_crew(agents: list, tasks: list, evidence: str) -> tuple[dict, TokenUsage]:
    from crewai import Crew, Process  # noqa: PLC0415

    crew = Crew(
        agents=agents,
        tasks=tasks,
        process=Process.sequential,
        memory=False,
        verbose=False,
    )
    result = crew.kickoff(inputs={"evidence": evidence})
    raw = parse_crew_json(getattr(result, "raw", "") or "")
    return raw, usage_from_crew_output(result)


def run_grading(
    group: list[str],
    solution_parsed: dict,
    student_parsed: dict,
    cfg: AppConfig,
    *,
    crew_type: str = "lean",
    client=None,  # accepted for signature parity; the crew builds its own LLM
) -> tuple[list[QuestionGrade], dict[str, int], TokenUsage, dict[str, bool]]:
    """Grade one question group. Returns (grade_items, qid_to_max, usage, injection_map)."""
    ct = CrewType.from_value(crew_type)
    model = cfg.model or DEFAULT_MODEL
    agents_cfg = load_crew_config("grading", "agents", cfg.assignment_name)
    tasks_cfg = load_crew_config("grading", "tasks", cfg.assignment_name)

    grader_def = agents_cfg["grader"]
    system_prompt = _persona_system_prompt(grader_def)
    messages, qid_to_max, qid_injection_suspect = build_group_prompt(
        group,
        solution_parsed,
        student_parsed,
        system_prompt,
        max_prompt_tokens=cfg.max_prompt_tokens,
        rubrics=cfg.rubrics,
        include_reference=cfg.include_reference_in_grading,
        assignment_name=cfg.assignment_name,
    )

    llm = build_crew_llm(model)
    chat_messages = _responses_messages_to_chat(messages)
    grounded_text = llm.call(chat_messages)
    if not isinstance(grounded_text, str):
        grounded_text = str(grounded_text)
    grounded_raw = parse_crew_json(grounded_text)

    usage = TokenUsage()

    try:
        grounded_items = grades_from_crew_json(grounded_raw, group)
    except ValueError:
        grounded_items = []

    if ct is CrewType.LEAN:
        if grounded_items:
            return grounded_items, qid_to_max, usage, qid_injection_suspect
        raise ValueError("Crew grounding produced no valid grades")

    text_evidence = _text_evidence_from_messages(messages)
    grounded_for_evidence = grounded_raw or {
        g.question_id: g.model_dump(mode="python") for g in grounded_items
    }
    evidence = (
        f"{text_evidence}\n\n"
        f"PROPOSED GRADES (from the vision grader):\n"
        f"{json.dumps(grounded_for_evidence, indent=2)}"
    )

    if ct is CrewType.VERIFIED:
        verifier = _agent(agents_cfg["verifier"], llm)
        t_verify = _task(tasks_cfg["verify"], verifier)
        raw, stage_usage = _run_text_crew([verifier], [t_verify], evidence)
        usage = usage.merged(stage_usage)
        try:
            items = grades_from_crew_json(raw or grounded_raw, group)
            return items, qid_to_max, usage, qid_injection_suspect
        except ValueError:
            if grounded_items:
                return grounded_items, qid_to_max, usage, qid_injection_suspect
            raise

    lens_keys = ["grader_correctness", "grader_rubric", "grader_leniency"]
    lens_agents = [_agent(agents_cfg[k], llm) for k in lens_keys]
    lens_tasks = [_task(tasks_cfg["verify"], a) for a in lens_agents]
    adjudicator = _agent(agents_cfg["adjudicator"], llm)
    t_adjudicate = _task(tasks_cfg["adjudicate"], adjudicator, context=lens_tasks)
    raw, stage_usage = _run_text_crew(
        lens_agents + [adjudicator], lens_tasks + [t_adjudicate], evidence
    )
    usage = usage.merged(stage_usage)
    try:
        items = grades_from_crew_json(raw or grounded_raw, group)
        return items, qid_to_max, usage, qid_injection_suspect
    except ValueError:
        if grounded_items:
            return grounded_items, qid_to_max, usage, qid_injection_suspect
        raise
