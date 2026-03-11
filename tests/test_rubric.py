"""Tests for rubric.py: LLM-based rubric generation."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from rubric import generate_rubrics


def _solution_parsed(qids: list[str]) -> dict:
    """Build solution_parsed.json structure."""
    sections = {}
    for qid in qids:
        sec, _ = qid.split(".")
        sections.setdefault(sec, {"questions": {}})
        sections[sec]["questions"][qid] = {
            "points": 2,
            "question_markdown": f"Q{qid}",
            "answer_code_concat": "x = 1",
            "answer_text_concat": "",
            "answer_markdown_concat": "",
        }
    return {"sections": sections}


class TestGenerateRubrics:
    def test_returns_expected_structure_and_normalizes_keys(self, tmp_path):
        """Mock LLM returns valid JSON; keys like Q4.1 are normalized to 4.1."""
        sol = _solution_parsed(["4.1", "4.2"])
        (tmp_path / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["4.1", "4.2"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "Q4.1": {"points": 2, "criteria": "Full marks: correct."},
            "q4.2": {"points": 2, "criteria": "Full marks: correct."},
        })

        with patch("rubric.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get_client.return_value = mock_client

            rubrics = generate_rubrics(config, client=mock_client)

        assert "4.1" in rubrics
        assert "4.2" in rubrics
        assert rubrics["4.1"]["points"] == 2
        assert rubrics["4.1"]["criteria"] == "Full marks: correct."
        assert rubrics["4.2"]["points"] == 2

    def test_fallback_when_points_missing_uses_solution(self, tmp_path):
        """When LLM omits points, use solution's points."""
        sol = _solution_parsed(["5.1"])
        (tmp_path / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["5.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps({
            "5.1": {"criteria": "Check the plot."},
        })

        with patch("rubric.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get_client.return_value = mock_client

            rubrics = generate_rubrics(config, client=mock_client)

        assert rubrics["5.1"]["points"] == 2
        assert rubrics["5.1"]["criteria"] == "Check the plot."

    def test_fallback_when_llm_fails(self, tmp_path):
        """When LLM call raises, fallback to solution points and error message."""
        sol = _solution_parsed(["6.1"])
        (tmp_path / "solution_parsed.json").write_text(json.dumps(sol), encoding="utf-8")

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["6.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        with patch("rubric.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("API error")
            mock_get_client.return_value = mock_client

            rubrics = generate_rubrics(config, client=mock_client)

        assert rubrics["6.1"]["points"] == 2
        assert "[generation failed" in rubrics["6.1"]["criteria"]

    def test_missing_solution_raises(self, tmp_path):
        """FileNotFoundError when solution_parsed.json does not exist."""
        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["1.1"]]},
        }
        with pytest.raises(FileNotFoundError, match="Solution parsed not found"):
            generate_rubrics(config)
