"""Tests for prompt_builder token estimation and prompt building."""

from config_models import RubricEntry, RubricItem

import prompt_builder


class _FakeEncoding:
    def __init__(self, value: int):
        self.value = value

    def encode(self, text: str):
        return [0] * self.value


class _FakeTikToken:
    def encoding_for_model(self, model: str):
        if model == "model-a":
            return _FakeEncoding(10)
        if model == "model-b":
            return _FakeEncoding(20)
        raise KeyError(model)

    def get_encoding(self, _name: str):
        return _FakeEncoding(30)


def test_estimate_tokens_caches_per_model(monkeypatch):
    monkeypatch.setattr(prompt_builder, "_enc_cache", {})
    monkeypatch.setattr(prompt_builder, "tiktoken", _FakeTikToken())

    a = prompt_builder.estimate_tokens("x", 0, model="model-a")
    b = prompt_builder.estimate_tokens("x", 0, model="model-b")

    assert a == 10
    assert b == 20


def test_load_prompt_works_outside_project_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    text = prompt_builder.load_prompt("grade_system")
    assert isinstance(text, str)
    assert text.strip()


def test_build_group_prompt_includes_rubric_from_rubric_entry_models():
    """Regression: AppConfig.rubrics values are RubricEntry models, not dicts."""
    solution_parsed = {
        "sections": {
            "1": {
                "questions": {
                    "1.1": {
                        "points": 10,
                        "question_markdown": "Q1.1",
                        "answer_code_concat": "",
                        "answer_text_concat": "",
                        "answer_markdown_concat": "",
                        "answer_cells": [],
                    }
                }
            }
        }
    }
    student_parsed = {
        "sections": {
            "1": {
                "questions": {
                    "1.1": {
                        "points": 10,
                        "question_markdown": "Q1.1",
                        "answer_code_concat": "x=1",
                        "answer_text_concat": "",
                        "answer_markdown_concat": "",
                        "answer_cells": [],
                    }
                }
            }
        }
    }
    rubrics = {
        "1.1": RubricEntry(
            points=10,
            items=[
                RubricItem(description="Wrong approach", deduction=5.0),
                RubricItem(description="No explanation", deduction=5.0),
            ],
        )
    }
    messages, qid_to_max = prompt_builder.build_group_prompt(
        ["1.1"],
        solution_parsed,
        student_parsed,
        "You are a grader.",
        rubrics=rubrics,
    )
    user = messages[1]["content"]
    text_blob = "\n".join(
        p["text"] for p in user if isinstance(p, dict) and p.get("type") == "input_text"
    )
    assert "RUBRIC (deduct from 10 pts):" in text_blob
    assert "Wrong approach: -5.0 pts" in text_blob
    assert "No explanation: -5.0 pts" in text_blob
    assert qid_to_max["1.1"] == 10


def test_build_group_prompt_includes_rubric_from_plain_dicts():
    rubrics = {
        "1.1": {
            "points": 10,
            "items": [
                {"description": "Off by one", "deduction": 3.0},
            ],
        }
    }
    solution_parsed = {
        "sections": {
            "1": {"questions": {"1.1": {"points": 10, "question_markdown": "Q"}}}
        }
    }
    student_parsed = {
        "sections": {
            "1": {
                "questions": {
                    "1.1": {
                        "points": 10,
                        "question_markdown": "Q",
                        "answer_code_concat": "1",
                        "answer_text_concat": "",
                        "answer_markdown_concat": "",
                        "answer_cells": [],
                    }
                }
            }
        }
    }
    messages, _ = prompt_builder.build_group_prompt(
        ["1.1"],
        solution_parsed,
        student_parsed,
        "sys",
        rubrics=rubrics,
    )
    user = messages[1]["content"]
    text_blob = "\n".join(
        p["text"] for p in user if isinstance(p, dict) and p.get("type") == "input_text"
    )
    assert "Off by one: -3.0 pts" in text_blob


def test_build_genai_detection_user_message_none_when_empty():
    from genai_detection import build_genai_detection_user_message

    student = {"sections": {"1": {"questions": {"1.1": {"question_markdown": ""}}}}}
    assert build_genai_detection_user_message(["1.1"], student, 8000) is None
