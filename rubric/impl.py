"""Backward-compatible facade; implementation lives in ``rubric.*`` submodules."""

from rubric.cli import main
from rubric.generate import generate_rubrics
from rubric.prompts import build_rubric_group_prompt
from rubric.review import review_rubrics

__all__ = [
    "build_rubric_group_prompt",
    "generate_rubrics",
    "main",
    "review_rubrics",
]
