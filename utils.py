"""Shared utilities for the AI Autograder pipeline."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def load_config(config_path: Path | None = None) -> dict:
    """Load config.yaml."""
    path = config_path or Path("config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(config: dict, config_path: Path | None = None) -> None:
    """Write config dict to YAML file."""
    path = config_path or Path("config.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------

def get_openai_client(max_retries: int = 5) -> OpenAI:
    """Initialize OpenAI client with .env key, with SDK-level retries."""
    load_dotenv()
    api_key = os.environ.get("key")
    if not api_key:
        raise ValueError("API key not found. Ensure .env contains 'key=your-api-key'")
    return OpenAI(api_key=api_key, max_retries=max_retries)


# ---------------------------------------------------------------------------
# Pydantic config validation models
# ---------------------------------------------------------------------------

class ParsingConfig(BaseModel):
    section_regex: str
    question_regex: str
    keep_images: bool = True


class GradingConfig(BaseModel):
    question_groups: list[list[str]]


class PromptsConfig(BaseModel):
    system: str


class AppConfig(BaseModel):
    assignment_name: str
    model: str
    solution_notebook: str
    submissions_dir: str = "output/submissions"
    parsed_dir: str = "output/parsed"
    output_dir: str = "output"
    workers: int = 1
    max_prompt_tokens: int = 80_000
    max_completion_tokens: int = 4_096
    parsing: ParsingConfig
    grading: GradingConfig
    prompts: PromptsConfig

    @field_validator("workers")
    @classmethod
    def workers_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("workers must be >= 1")
        return v
