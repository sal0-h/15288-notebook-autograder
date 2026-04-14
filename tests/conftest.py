"""Pytest configuration and shared fixtures."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from config_models import DEFAULT_MODEL, AppConfig, ensure_app_config  # noqa: E402


@pytest.fixture
def sample_config(tmp_path: Path) -> dict:
    """Minimal valid config dict with tmp_path-based output directories."""
    output_dir = tmp_path / "output" / "test_assignment"
    output_dir.mkdir(parents=True)
    return {
        "assignment_name": "test_assignment",
        "model": DEFAULT_MODEL,
        "output_dir": str(output_dir),
        "submissions_dir": str(output_dir / "submissions"),
        "parsed_dir": str(output_dir / "parsed"),
        "grading": {"question_groups": [["1.1"]]},
        "rubrics": {},
        "max_prompt_tokens": 80000,
        "max_completion_tokens": 4096,
    }


@pytest.fixture
def sample_app_config(sample_config: dict) -> AppConfig:
    """Sample AppConfig built from sample_config."""
    return ensure_app_config(sample_config)


@pytest.fixture
def sample_parsed_notebook() -> dict:
    """Minimal parsed notebook dict with one section/question."""
    return {
        "student_name": "test_student",
        "sections": {
            "1": {
                "overview_markdown": "",
                "questions": {
                    "1.1": {
                        "points": 5,
                        "question_markdown": "What is 2+2?",
                        "answer_code_concat": "print(2+2)",
                        "answer_text_concat": "4",
                        "answer_markdown_concat": "",
                        "answer_cells": [],
                    }
                },
            }
        },
        "duplicate_qids": [],
    }


@pytest.fixture
def mock_openai_client() -> MagicMock:
    """Mock OpenAI client for tests that call LLM functions."""
    return MagicMock()
