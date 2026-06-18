"""Load agent/task definitions from agent_prompts/.

Mirrors the DEFAULT + per-assignment fallback used by prompt_builder.load_prompt, but
for the agentic crews. Kept entirely separate from the prompts/ directory so the
existing simple-LLM prompts are never touched.

Layout:
    agent_prompts/
      DEFAULT/
        rubric/agents.yaml    rubric/tasks.yaml
        grading/agents.yaml   grading/tasks.yaml
      {assignment_name}/      # optional overrides, same structure
"""

from __future__ import annotations

from pathlib import Path

import yaml

_BASE = Path(__file__).resolve().parent.parent / "agent_prompts"


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_crew_config(
    family: str, kind: str, assignment_name: str | None = None
) -> dict:
    """Load one crew config file.

    Args:
        family: 'rubric' or 'grading'.
        kind: 'agents' or 'tasks'.
        assignment_name: optional override scope, checked before DEFAULT.

    Returns the parsed YAML mapping. Raises FileNotFoundError if neither the
    assignment override nor the DEFAULT file exists.
    """
    filename = f"{kind}.yaml"

    if assignment_name:
        override = _BASE / assignment_name / family / filename
        if override.exists():
            return _read_yaml(override)

    default_path = _BASE / "DEFAULT" / family / filename
    if not default_path.exists():
        raise FileNotFoundError(
            f"Missing crew config '{family}/{filename}'. Looked in "
            f"{f'agent_prompts/{assignment_name}/{family}/ and ' if assignment_name else ''}"
            f"agent_prompts/DEFAULT/{family}/."
        )
    return _read_yaml(default_path)
