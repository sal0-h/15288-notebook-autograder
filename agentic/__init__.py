"""Agentic (CrewAI) grading and rubric subsystem.

This package is OPT-IN. It is only imported when config.agentic.enabled is true,
so the rest of the autograder runs without crewai installed. All heavy imports of
``crewai`` happen lazily inside functions for the same reason.

Layout:
- llm.py        : build a crewai.LLM bound to the project's OpenAI key + temperature policy.
- crew_types.py : the selectable crew profiles (lean | verified | panel).
- loader.py     : load agent/task definitions from agent_prompts/ (DEFAULT + per-assignment).
- postprocess.py: shared JSON->validated-shape helpers (mirror the simple-LLM path).
- rubric_crew.py: rubric generation + review crews.
- grading_crew.py: grading crews (multimodal).
"""

from agentic.crew_types import CrewType

__all__ = ["CrewType"]
