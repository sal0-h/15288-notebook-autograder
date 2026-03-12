"""Shared utilities for the AI Autograder pipeline."""

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

from openai import OpenAI
from pydantic import BaseModel, field_validator, model_validator

# Default model for grading when not specified in config
DEFAULT_MODEL = "gpt-5-mini"

# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------


def _project_root(config_path: Path | None) -> Path:
    """Project root = directory containing the root config.yaml."""
    return (config_path or Path("config.yaml")).resolve().parent


def _is_assignment_config(path: Path) -> bool:
    """True if path is output/{assignment_name}/config.yaml."""
    parts = path.resolve().parts
    return len(parts) >= 3 and "output" in parts and path.name == "config.yaml"


def load_config(config_path: Path | None = None) -> dict:
    """Load config. Uses output-first layout when available:
    - Root config.yaml: minimal pointer (assignment_name, output_dir)
    - Primary config: output/{assignment_name}/config.yaml (full config)
    Paths are resolved relative to project root. Outputs scoped under output/{assignment_name}/.
    """
    root_path = (config_path or Path("config.yaml")).resolve()

    # If explicitly given an assignment config path (e.g. --config output/X/config.yaml), load it directly
    if (
        config_path is not None
        and _is_assignment_config(root_path)
        and root_path.exists()
    ):
        project_root = root_path.parent.parent.parent  # output/X/config.yaml -> project
        with open(root_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        config_root = project_root
        assignment_name = (
            re.sub(r'[/\\:*?"<>|.]', "_", cfg.get("assignment_name", "default")).strip(
                "_"
            )
            or "default"
        )
        base_output = cfg.get("output_dir", "output")
        if Path(base_output).is_absolute():
            base_output = "output"
    else:
        project_root = root_path.parent

        # Load root config (pointer or legacy full config)
        if root_path.exists():
            with open(root_path, "r", encoding="utf-8") as f:
                root_cfg = yaml.safe_load(f) or {}
        else:
            root_cfg = {}

        assignment_name = (
            re.sub(
                r'[/\\:*?"<>|.]', "_", root_cfg.get("assignment_name", "default")
            ).strip("_")
            or "default"
        )
        base_output = root_cfg.get("output_dir", "output")
        assignment_config_path = (
            project_root / base_output / assignment_name / "config.yaml"
        )

        # Prefer assignment-specific config if it exists
        if assignment_config_path.exists():
            with open(assignment_config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            config_root = (
                project_root  # paths in assignment config are relative to project root
            )
            # Prompts and root-only grading controls live in root; merge them in
            if root_cfg.get("prompts"):
                cfg["prompts"] = root_cfg["prompts"]
            if "rubric_review" in root_cfg:
                cfg["rubric_review"] = root_cfg["rubric_review"]
            if "include_reference_in_grading" in root_cfg:
                cfg["include_reference_in_grading"] = root_cfg[
                    "include_reference_in_grading"
                ]
        else:
            cfg = dict(root_cfg)
            config_root = project_root

    # When loading from explicit assignment config path, merge prompts from root
    if config_path is not None and _is_assignment_config(root_path):
        root_config_path = project_root / "config.yaml"
        if root_config_path.exists():
            with open(root_config_path, "r", encoding="utf-8") as f:
                root_cfg = yaml.safe_load(f) or {}
            if root_cfg.get("prompts"):
                cfg["prompts"] = root_cfg["prompts"]
            if "rubric_review" in root_cfg:
                cfg["rubric_review"] = root_cfg["rubric_review"]
            if "include_reference_in_grading" in root_cfg:
                cfg["include_reference_in_grading"] = root_cfg[
                    "include_reference_in_grading"
                ]
        elif "prompts" not in cfg or not cfg.get("prompts"):
            cfg.setdefault(
                "prompts",
                {"system": "You are an expert instructor. Return valid JSON only."},
            )

    # Resolve paths relative to project root
    for key in ("solution_notebook", "submissions_dir", "parsed_dir", "output_dir"):
        if key in cfg and cfg[key] and not Path(cfg[key]).is_absolute():
            cfg[key] = str((config_root / cfg[key]).resolve())

    # Output dir is always output/{assignment_name}/
    assignment_root = (project_root / base_output / assignment_name).resolve()
    cfg["output_dir"] = str(assignment_root)
    cfg["submissions_dir"] = str(assignment_root / "submissions")
    cfg["parsed_dir"] = str(assignment_root / "parsed")
    cfg["assignment_name"] = assignment_name
    return cfg


def save_config(config: dict, config_path: Path | None = None) -> None:
    """Save config using output-first layout:
    - Full config → output/{assignment_name}/config.yaml
    - Root config.yaml → minimal pointer (assignment_name, output_dir)"""
    validated = AppConfig.model_validate(config)
    cfg = validated.model_dump()
    cfg["output_dir"] = "output"
    cfg.pop("submissions_dir", None)
    cfg.pop("parsed_dir", None)

    project_root = _project_root(config_path)
    assignment_name = (
        re.sub(r'[/\\:*?"<>|.]', "_", cfg.get("assignment_name", "default")).strip("_")
        or "default"
    )
    base_output = cfg.get("output_dir", "output")
    assignment_config_path = (
        project_root / base_output / assignment_name / "config.yaml"
    )

    # Relativize solution_notebook for portability (relative to project root)
    sol = cfg.get("solution_notebook", "")
    if sol:
        sol_path = Path(sol)
        if sol_path.is_absolute():
            try:
                cfg["solution_notebook"] = str(sol_path.relative_to(project_root))
            except ValueError:
                pass

    # Save full config to output/{assignment_name}/config.yaml
    # Root-only controls are excluded to keep assignment config focused on runtime artifacts.
    assignment_cfg = {
        k: v
        for k, v in cfg.items()
        if k not in ("prompts", "rubric_review", "include_reference_in_grading")
    }
    assignment_config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(assignment_config_path, "w", encoding="utf-8") as f:
        yaml.dump(assignment_cfg, f, default_flow_style=False, allow_unicode=True)

    # Save root: only update assignment_name and output_dir; leave prompts untouched
    # to avoid reformatting the prompts block on every save.
    root_path = (config_path or Path("config.yaml")).resolve()
    root_content = root_path.read_text(encoding="utf-8") if root_path.exists() else ""
    root_content = re.sub(
        r"^assignment_name:\s*.+",
        f"assignment_name: {assignment_name}",
        root_content,
        count=1,
        flags=re.MULTILINE,
    )
    root_content = re.sub(
        r"^output_dir:\s*.+",
        f"output_dir: {base_output}",
        root_content,
        count=1,
        flags=re.MULTILINE,
    )
    # If prompts or root-only controls changed, do a full write.
    existing = yaml.safe_load(root_content) if root_content.strip() else {}
    existing_prompts = existing.get("prompts", {})
    new_prompts = cfg.get("prompts", {})
    existing_review = existing.get("rubric_review", True)
    new_review = cfg.get("rubric_review", True)
    existing_include_ref = existing.get("include_reference_in_grading", False)
    new_include_ref = cfg.get("include_reference_in_grading", False)
    if (
        existing_prompts != new_prompts
        or existing_review != new_review
        or existing_include_ref != new_include_ref
    ):
        root_cfg = {
            "assignment_name": assignment_name,
            "output_dir": base_output,
            "rubric_review": new_review,
            "include_reference_in_grading": new_include_ref,
            "prompts": new_prompts,
        }
        with open(root_path, "w", encoding="utf-8") as f:
            yaml.dump(root_cfg, f, default_flow_style=False, allow_unicode=True)
    else:
        root_path.write_text(root_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# OpenAI client
# ---------------------------------------------------------------------------


# GPT-5 reasoning models only support temperature=1. Others can use 0 for deterministic output.
def temperature_for_model(model: str) -> float:
    """Use 0 when model supports it (deterministic); else 1. GPT-5 family only supports 1."""
    if model.startswith("gpt-5"):
        return 1.0
    return 0.0


def get_openai_client(max_retries: int = 5) -> OpenAI:
    """Initialize OpenAI client with .env key, with SDK-level retries.
    Checks 'key' first (from .env), then OPENAI_API_KEY as fallback."""
    load_dotenv()
    api_key = os.environ.get("key") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "API key not found. Set 'key=your-api-key' in .env or OPENAI_API_KEY environment variable."
        )
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
    grade_only: list[str] | None = (
        None  # If set, only grade these question IDs; others get 0 [skipped]
    )


class RubricItem(BaseModel):
    description: str
    deduction: float  # positive number, e.g. 1.0 means "-1 point"

    @field_validator("deduction", mode="before")
    @classmethod
    def coerce_deduction(cls, v) -> float:
        try:
            return abs(float(v))
        except (TypeError, ValueError):
            return 0.0


class RubricEntry(BaseModel):
    points: int
    items: list[RubricItem] = []

    @field_validator("points", mode="before")
    @classmethod
    def coerce_points(cls, v) -> int:
        if v is None:
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    @model_validator(mode="after")
    def validate_deductions_sum(self) -> "RubricEntry":
        total_deductions = sum(i.deduction for i in self.items)
        if self.items and abs(total_deductions - self.points) > 0.01:
            raise ValueError(
                f"Deductions ({total_deductions}) must sum to exactly {self.points} points"
            )
        return self


class PromptsConfig(BaseModel):
    system: str
    rubric_system: str = ""


class AppConfig(BaseModel):
    assignment_name: str
    model: str
    rubric_model: str = ""  # If set, used for rubric generation; else uses model
    rubric_review: bool = (
        True  # If True, run optional second LLM pass to soften rubric wording
    )
    include_reference_in_grading: bool = (
        False  # If True, include raw reference solution in grading prompt
    )
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
