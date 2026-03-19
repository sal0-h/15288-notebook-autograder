"""Job dataclasses for rubric generation and review workers."""

from __future__ import annotations

from dataclasses import dataclass

from openai import OpenAI

from utils import AppConfig

GENERATION_FAILED = "[generation failed]"


@dataclass(frozen=True)
class RubricGenJob:
    idx: int
    group: list[str]
    solution_parsed: dict
    config: AppConfig
    rubric_prompt: str
    model: str
    max_completion_tokens: int
    client: OpenAI | None


@dataclass(frozen=True)
class RubricReviewJob:
    group: list[str]
    rubrics: dict[str, dict]
    solution_parsed: dict
    config: AppConfig
    review_prompt: str
    model: str
    max_completion_tokens: int
    client: OpenAI | None
