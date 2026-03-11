"""Shared utilities for the AI Autograder pipeline."""

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, field_validator


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def load_config(config_path: Path | None = None) -> dict:
    """Load config.yaml. Paths in config are resolved relative to the config file's directory.
    All outputs are scoped under output_dir/{assignment_name}/ so grading multiple
    assignments does not overwrite each other."""
    path = (config_path or Path("config.yaml")).resolve()
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    config_root = path.parent
    for key in ("solution_notebook", "submissions_dir", "parsed_dir", "output_dir"):
        if key in cfg and cfg[key] and not Path(cfg[key]).is_absolute():
            cfg[key] = str((config_root / cfg[key]).resolve())

    # Scope all outputs under output_dir/{assignment_name}/
    base_output = Path(cfg.get("output_dir", "output"))
    assignment_name = re.sub(r'[/\\:*?"<>|.]', "_", cfg.get("assignment_name", "default"))
    assignment_root = base_output / assignment_name
    cfg["output_dir"] = str(assignment_root)
    cfg["submissions_dir"] = str(assignment_root / "submissions")
    cfg["parsed_dir"] = str(assignment_root / "parsed")
    return cfg


def save_config(config: dict, config_path: Path | None = None) -> None:
    """Write config dict to YAML file. Reverses assignment-scoped paths so the saved
    config stores the base output_dir (not output_dir/assignment_name)."""
    cfg = dict(config)
    # Reverse assignment-scoping so we save base paths
    if cfg.get("output_dir") and cfg.get("assignment_name"):
        p = Path(cfg["output_dir"])
        if p.name == cfg["assignment_name"]:
            cfg["output_dir"] = str(p.parent)
    cfg.pop("submissions_dir", None)
    cfg.pop("parsed_dir", None)
    path = config_path or Path("config.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True)


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

    @field_validator("section_regex", "question_regex")
    @classmethod
    def validate_regex(cls, v: str) -> str:
        import re
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"Invalid regex: {e}") from e
        return v


class GradingConfig(BaseModel):
    question_groups: list[list[str]]
    grade_only: list[str] | None = None  # If set, only grade these question IDs; others get 0 [skipped]


class RubricEntry(BaseModel):
    points: int
    criteria: str = ""

    @field_validator("points", mode="before")
    @classmethod
    def coerce_points(cls, v) -> int:
        if v is None:
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    @field_validator("criteria", mode="before")
    @classmethod
    def coerce_criteria(cls, v) -> str:
        return str(v) if v is not None else ""


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
    rubrics: dict[str, RubricEntry] = {}
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
