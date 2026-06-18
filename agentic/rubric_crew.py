"""Rubric generation and review crews (text-only — no images).

Two entry points, mirroring the two simple-LLM phases:
- run_rubric_generation : author-led draft of a rubric for a question group.
- run_rubric_review     : leniency/fairness pass over an existing rubric.

Each returns (raw_json: dict, usage: TokenUsage). The caller applies normalization
via agentic.postprocess so the output shape matches the simple path exactly.

Crew profiles (agentic.crew_types.CrewType):
  generation  lean     -> author
              verified -> author -> validator
              panel    -> author -> fairness_auditor -> calibrator -> synthesizer
  review      lean     -> reviewer
              verified -> reviewer -> validator
              panel    -> fairness_auditor -> calibrator -> synthesizer
"""

from __future__ import annotations

import json

from config_models import AppConfig, DEFAULT_MODEL, RubricEntry
from prompt_builder import get_question_data
from rubric_generate import build_rubric_group_prompt
from token_usage import TokenUsage

from agentic.crew_types import CrewType
from agentic.llm import build_crew_llm
from agentic.loader import load_crew_config
from agentic.postprocess import parse_crew_json, usage_from_crew_output


def _rubric_as_dict(entry: RubricEntry | dict) -> dict:
    if isinstance(entry, RubricEntry):
        return entry.model_dump()
    return entry


def _review_evidence(
    group: list[str],
    rubrics: dict[str, RubricEntry | dict],
    solution_parsed: dict,
) -> tuple[str, dict[str, dict]]:
    """Question text + current rubric JSON for each question."""
    parts: list[str] = []
    group_rubrics: dict[str, dict] = {}
    for qid in group:
        if qid not in rubrics:
            continue
        rd = _rubric_as_dict(rubrics[qid])
        sol_q = get_question_data(solution_parsed, qid)
        q_md = (sol_q or {}).get("question_markdown", f"Question {qid}")
        pts = rd.get("points", 0)
        parts.append(f"--- QUESTION {qid} ({pts} pts) ---")
        parts.append(f"QUESTION TEXT:\n{q_md}\n")
        parts.append(f"CURRENT RUBRIC:\n{json.dumps({qid: rd}, indent=2)}\n")
        group_rubrics[qid] = rd
    return "\n".join(parts), group_rubrics


def _agent(defn: dict, llm, **extra):
    from crewai import Agent  # noqa: PLC0415

    return Agent(
        role=defn["role"],
        goal=defn["goal"],
        backstory=defn.get("backstory", ""),
        llm=llm,
        verbose=False,
        allow_delegation=False,
        **extra,
    )


def _task(defn: dict, agent, context: list | None = None):
    from crewai import Task  # noqa: PLC0415

    return Task(
        description=defn["description"],
        expected_output=defn["expected_output"],
        agent=agent,
        context=context or [],
    )


def _run_crew(agents: list, tasks: list, evidence: str) -> tuple[dict, TokenUsage]:
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


def run_rubric_generation(
    group: list[str],
    solution_parsed: dict,
    cfg: AppConfig,
    *,
    crew_type: str = "lean",
) -> tuple[dict, TokenUsage]:
    """Draft a rubric for one question group. Returns (raw_json, usage)."""
    if not group:
        return {}, TokenUsage()

    ct = CrewType.from_value(crew_type)
    model = cfg.rubric_model or cfg.model or DEFAULT_MODEL
    llm = build_crew_llm(model)
    agents_cfg = load_crew_config("rubric", "agents", cfg.assignment_name)
    tasks_cfg = load_crew_config("rubric", "tasks", cfg.assignment_name)
    evidence = build_rubric_group_prompt(
        group, solution_parsed, assignment_name=cfg.assignment_name
    )

    author = _agent(agents_cfg["author"], llm)
    t_generate = _task(tasks_cfg["generate"], author)

    if ct is CrewType.LEAN:
        agents, tasks = [author], [t_generate]
    elif ct is CrewType.VERIFIED:
        validator = _agent(agents_cfg["validator"], llm)
        t_validate = _task(tasks_cfg["validate"], validator, context=[t_generate])
        agents, tasks = [author, validator], [t_generate, t_validate]
    else:  # PANEL
        auditor = _agent(agents_cfg["fairness_auditor"], llm)
        calibrator = _agent(agents_cfg["calibrator"], llm)
        synthesizer = _agent(agents_cfg["synthesizer"], llm)
        t_audit = _task(tasks_cfg["refine"], auditor, context=[t_generate])
        t_calibrate = _task(tasks_cfg["refine"], calibrator, context=[t_audit])
        t_synth = _task(
            tasks_cfg["synthesize"],
            synthesizer,
            context=[t_generate, t_audit, t_calibrate],
        )
        agents = [author, auditor, calibrator, synthesizer]
        tasks = [t_generate, t_audit, t_calibrate, t_synth]

    return _run_crew(agents, tasks, evidence)


def run_rubric_review(
    group: list[str],
    rubrics: dict[str, RubricEntry | dict],
    solution_parsed: dict,
    cfg: AppConfig,
    *,
    crew_type: str = "lean",
) -> tuple[dict, TokenUsage, dict[str, dict]]:
    """Review an existing rubric for a group. Returns (raw_json, usage, group_rubrics)."""
    evidence, group_rubrics = _review_evidence(group, rubrics, solution_parsed)
    if not group_rubrics:
        return {}, TokenUsage(), {}

    ct = CrewType.from_value(crew_type)
    model = cfg.rubric_model or cfg.model or DEFAULT_MODEL
    llm = build_crew_llm(model)
    agents_cfg = load_crew_config("rubric", "agents", cfg.assignment_name)
    tasks_cfg = load_crew_config("rubric", "tasks", cfg.assignment_name)

    if ct is CrewType.LEAN:
        reviewer = _agent(agents_cfg["reviewer"], llm)
        t_refine = _task(tasks_cfg["refine"], reviewer)
        agents, tasks = [reviewer], [t_refine]
    elif ct is CrewType.VERIFIED:
        reviewer = _agent(agents_cfg["reviewer"], llm)
        validator = _agent(agents_cfg["validator"], llm)
        t_refine = _task(tasks_cfg["refine"], reviewer)
        t_validate = _task(tasks_cfg["validate"], validator, context=[t_refine])
        agents, tasks = [reviewer, validator], [t_refine, t_validate]
    else:  # PANEL
        auditor = _agent(agents_cfg["fairness_auditor"], llm)
        calibrator = _agent(agents_cfg["calibrator"], llm)
        synthesizer = _agent(agents_cfg["synthesizer"], llm)
        t_audit = _task(tasks_cfg["refine"], auditor)
        t_calibrate = _task(tasks_cfg["refine"], calibrator, context=[t_audit])
        t_synth = _task(
            tasks_cfg["synthesize"], synthesizer, context=[t_audit, t_calibrate]
        )
        agents = [auditor, calibrator, synthesizer]
        tasks = [t_audit, t_calibrate, t_synth]

    raw, usage = _run_crew(agents, tasks, evidence)
    return raw, usage, group_rubrics
