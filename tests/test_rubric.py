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
    def test_uses_configured_rubric_generation_prompt(self, tmp_path, monkeypatch):
        """Rubric generation should use the prompt text returned by load_prompt."""
        sol = _solution_parsed(["1.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        custom_prompt = "CUSTOM RUBRIC SYSTEM PROMPT"

        monkeypatch.setattr(
            "rubric.impl.load_prompt", lambda name, assignment_name=None: custom_prompt
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["1.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {
                "1.1": {
                    "points": 2,
                    "items": [{"description": "Correct", "deduction": 2.0}],
                }
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        generate_rubrics(config, client=mock_client)

        call = mock_client.chat.completions.create.call_args
        assert call.kwargs["messages"][0]["content"] == custom_prompt

    def test_uses_configured_rubric_review_prompt(self, tmp_path, monkeypatch):
        """Rubric review second pass should use review_system prompt from load_prompt."""
        sol = {
            "sections": {
                "1": {
                    "questions": {
                        "1.1": {
                            "points": 2,
                            "question_markdown": "Q1.1",
                            "answer_code_concat": "x = 1",
                            "answer_text_concat": "",
                            "answer_markdown_concat": "",
                        }
                    }
                }
            }
        }
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        custom_review_prompt = "CUSTOM RUBRIC REVIEW PROMPT"
        monkeypatch.setattr(
            "rubric.impl.load_prompt",
            lambda name, assignment_name=None: (
                custom_review_prompt if name == "review_system" else "OTHER"
            ),
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["1.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
            "rubric_review": True,
        }

        gen_response = MagicMock()
        gen_response.choices = [MagicMock()]
        gen_response.choices[0].message.content = json.dumps(
            {
                "1.1": {
                    "points": 2,
                    "items": [{"description": "Generated", "deduction": 2.0}],
                }
            }
        )

        review_response = MagicMock()
        review_response.choices = [MagicMock()]
        review_response.choices[0].message.content = json.dumps(
            {
                "1.1": {
                    "points": 2,
                    "items": [{"description": "Reviewed", "deduction": 2.0}],
                }
            }
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            gen_response,
            review_response,
        ]

        rubrics = generate_rubrics(config, client=mock_client)

        calls = mock_client.chat.completions.create.call_args_list
        assert len(calls) == 2
        assert calls[1].kwargs["messages"][0]["content"] == custom_review_prompt
        assert rubrics["1.1"]["items"][0]["description"] == "Reviewed"

    def test_returns_expected_structure_and_normalizes_keys(self, tmp_path):
        """Mock LLM returns valid JSON; keys like Q4.1 are normalized to 4.1."""
        sol = _solution_parsed(["4.1", "4.2"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["4.1", "4.2"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {
                "Q4.1": {
                    "points": 2,
                    "items": [
                        {"description": "Correct output", "deduction": 1.0},
                        {"description": "Correct method", "deduction": 1.0},
                    ],
                },
                "q4.2": {
                    "points": 2,
                    "items": [
                        {"description": "Full marks: correct.", "deduction": 2.0},
                    ],
                },
            }
        )

        with patch("rubric.impl.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get_client.return_value = mock_client

            rubrics = generate_rubrics(config, client=mock_client)

        assert "4.1" in rubrics
        assert "4.2" in rubrics
        assert rubrics["4.1"]["points"] == 2
        assert rubrics["4.1"]["items"][0]["description"] == "Correct output"
        assert rubrics["4.1"]["items"][0]["deduction"] == 1.0
        assert rubrics["4.2"]["points"] == 2

    def test_fallback_when_points_missing_uses_solution(self, tmp_path):
        """When LLM omits points, use solution's points."""
        sol = _solution_parsed(["5.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["5.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {
                "5.1": {
                    "items": [{"description": "Check the plot.", "deduction": 2.0}]
                },
            }
        )

        with patch("rubric.impl.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get_client.return_value = mock_client

            rubrics = generate_rubrics(config, client=mock_client)

        assert rubrics["5.1"]["points"] == 2
        assert rubrics["5.1"]["items"][0]["description"] == "Check the plot."

    def test_fallback_when_llm_fails(self, tmp_path):
        """When LLM call raises, fallback to solution points and error message."""
        sol = _solution_parsed(["6.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["6.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        with patch("rubric.impl.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.side_effect = Exception("API error")
            mock_get_client.return_value = mock_client

            rubrics = generate_rubrics(config, client=mock_client)

        assert rubrics["6.1"]["points"] == 2
        assert rubrics["6.1"]["items"][0]["description"] == "[generation failed]"
        assert rubrics["6.1"]["items"][0]["deduction"] == 2

    def test_deductions_rescaled_when_off(self, tmp_path):
        """When LLM returns items summing to wrong total, rescale to match points."""
        sol = _solution_parsed(["7.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["7.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        # LLM returns 3 total deductions for a 2-point question
        mock_response.choices[0].message.content = json.dumps(
            {
                "7.1": {
                    "points": 2,
                    "items": [
                        {"description": "A", "deduction": 1.0},
                        {"description": "B", "deduction": 1.0},
                        {"description": "C", "deduction": 1.0},
                    ],
                },
            }
        )

        with patch("rubric.impl.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get_client.return_value = mock_client

            rubrics = generate_rubrics(config, client=mock_client)

        assert rubrics["7.1"]["points"] == 2
        total = sum(i["deduction"] for i in rubrics["7.1"]["items"])
        assert abs(total - 2.0) < 0.01

    def test_missing_solution_raises(self, tmp_path):
        """FileNotFoundError when solution_parsed.json does not exist."""
        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["1.1"]]},
        }
        with pytest.raises(FileNotFoundError, match="Solution parsed not found"):
            generate_rubrics(config)

    def test_group_indices_merges_into_existing(self, tmp_path):
        """When group_indices is set, only generates for those groups and merges into existing."""
        sol = _solution_parsed(["4.1", "4.2", "5.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        existing_rubrics = {
            "4.1": {
                "points": 2,
                "items": [{"description": "Existing 4.1", "deduction": 2.0}],
            },
            "4.2": {
                "points": 2,
                "items": [{"description": "Existing 4.2", "deduction": 2.0}],
            },
        }

        config = {
            "output_dir": str(tmp_path),
            "grading": {"question_groups": [["4.1", "4.2"], ["5.1"]]},
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
            "rubrics": existing_rubrics,
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {
                "5.1": {
                    "points": 2,
                    "items": [{"description": "New 5.1", "deduction": 2.0}],
                }
            }
        )

        with patch("rubric.impl.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get_client.return_value = mock_client

            rubrics = generate_rubrics(config, client=mock_client, group_indices=[1])

        # Existing rubrics preserved (group 0 not processed)
        assert rubrics["4.1"]["items"][0]["description"] == "Existing 4.1"
        assert rubrics["4.2"]["items"][0]["description"] == "Existing 4.2"
        # Group 1 (5.1) generated
        assert rubrics["5.1"]["items"][0]["description"] == "New 5.1"

    def test_group_indices_are_applied_before_grade_only_filter(self, tmp_path):
        """group_indices refer to original question_groups even when grade_only is set."""
        sol = _solution_parsed(["1.1", "1.2", "2.1"])
        (tmp_path / "solution_parsed.json").write_text(
            json.dumps(sol), encoding="utf-8"
        )

        config = {
            "output_dir": str(tmp_path),
            "grading": {
                "question_groups": [["1.1"], ["1.2"], ["2.1"]],
                "grade_only": ["2.1"],
            },
            "model": "gpt-4o",
            "max_completion_tokens": 4096,
            "rubrics": {},
        }

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = json.dumps(
            {
                "2.1": {
                    "points": 2,
                    "items": [{"description": "Generated 2.1", "deduction": 2.0}],
                }
            }
        )

        with patch("rubric.impl.get_openai_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_get_client.return_value = mock_client

            rubrics = generate_rubrics(config, client=mock_client, group_indices=[2])

        assert "2.1" in rubrics
        assert rubrics["2.1"]["items"][0]["description"] == "Generated 2.1"
