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
    messages, qid_to_max, inj = prompt_builder.build_group_prompt(
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
    assert "RUBRIC" in text_blob and "evaluate EACH criterion" in text_blob
    assert "Wrong approach: -5.0 pts" in text_blob
    assert "No explanation: -5.0 pts" in text_blob
    assert qid_to_max["1.1"] == 10
    assert inj.get("1.1") is False


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
    messages, _, inj = prompt_builder.build_group_prompt(
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
    assert inj.get("1.1") is False


def test_build_group_prompt_sandwich_and_rubric_mirror_copy():
    """Post-student anchor + anti–rubric-mirroring lines are present (injection depth)."""
    sol = {
        "sections": {
            "1": {
                "questions": {
                    "1.1": {
                        "points": 1,
                        "question_markdown": "Q",
                        "answer_code_concat": "x=1",
                        "answer_text_concat": "",
                        "answer_markdown_concat": "",
                        "answer_cells": [],
                    }
                }
            }
        }
    }
    stu = {
        "sections": {
            "1": {
                "questions": {
                    "1.1": {
                        "points": 1,
                        "question_markdown": "Q",
                        "answer_code_concat": "x=1",
                        "answer_text_concat": "",
                        "answer_markdown_concat": "",
                        "answer_cells": [],
                    }
                }
            }
        }
    }
    messages, _, _ = prompt_builder.build_group_prompt(["1.1"], sol, stu, "sys")
    blob = "\n".join(
        p["text"]
        for p in messages[1]["content"]
        if isinstance(p, dict) and p.get("type") == "input_text"
    )
    assert "mirror rubric language" in blob
    assert "End of student evidence for question 1.1" in blob


def _make_parsed(qid: str, q_md: str, **answer_fields) -> dict:
    """Build a minimal parsed notebook dict for a single question."""
    sec, qnum = qid.split(".")
    q = {
        "points": 5,
        "question_markdown": q_md,
        "answer_code_concat": "",
        "answer_text_concat": "",
        "answer_markdown_concat": "",
        "answer_cells": [],
        **answer_fields,
    }
    return {"sections": {sec: {"questions": {qid: q}}}}


def _text_blob(messages: list) -> str:
    return "\n".join(
        p["text"]
        for p in messages[1]["content"]
        if isinstance(p, dict) and p.get("type") == "input_text"
    )


def test_inline_answer_in_question_cell_appears_in_student_block():
    """Student's extra content in the question cell must be in the student evidence."""
    solution = _make_parsed("1.1", "What is the answer to life?")
    student = _make_parsed("1.1", "What is the answer to life?\n\n**Answer:** 42.")

    messages, _, _ = prompt_builder.build_group_prompt(
        ["1.1"], solution, student, "sys"
    )
    blob = _text_blob(messages)

    # Question header uses the SOLUTION text
    assert "What is the answer to life?" in blob
    # Inline answer is in the student submission block
    assert "Inline answer in question cell" in blob
    assert "42." in blob


def test_inline_answer_inside_delimiters():
    """The inline question cell content must appear inside the student delimiters."""
    solution = _make_parsed("1.1", "Describe the algorithm.")
    student = _make_parsed("1.1", "Describe the algorithm.\n\nThe algorithm is BFS.")

    messages, _, _ = prompt_builder.build_group_prompt(
        ["1.1"], solution, student, "sys"
    )
    blob = _text_blob(messages)

    submission_start = blob.index("<<<STUDENT_SUBMISSION>>>")
    submission_end = blob.index("<<<END_STUDENT_SUBMISSION>>>")
    submission_block = blob[submission_start:submission_end]

    assert "Inline answer in question cell" in submission_block
    assert "BFS" in submission_block


def test_no_inline_section_when_question_cells_identical():
    """When the student's question cell matches the solution exactly, no inline section."""
    solution = _make_parsed("1.1", "Q: What is 1+1?")
    student = _make_parsed("1.1", "Q: What is 1+1?", answer_code_concat="x = 2")

    messages, _, _ = prompt_builder.build_group_prompt(
        ["1.1"], solution, student, "sys"
    )
    blob = _text_blob(messages)

    assert "Inline answer in question cell" not in blob


def test_header_still_uses_solution_markdown_when_inline_differs():
    """Question header must always show solution text even when student cell differs."""
    solution = _make_parsed("1.1", "SOLUTION QUESTION TEXT")
    student = _make_parsed("1.1", "STUDENT MODIFIED QUESTION\n\nMy answer is here.")

    messages, _, _ = prompt_builder.build_group_prompt(
        ["1.1"], solution, student, "sys"
    )
    blob = _text_blob(messages)

    assert "SOLUTION QUESTION TEXT" in blob
    # Student-modified wording must not appear in the header portion (before submission block)
    header_section = blob[: blob.index("<<<STUDENT_SUBMISSION>>>")]
    assert "STUDENT MODIFIED QUESTION" not in header_section
    # But the student text does appear inside the student block
    assert "STUDENT MODIFIED QUESTION" in blob


def test_inline_answer_only_submission_not_marked_no_submission():
    """A student who only answered inline should not see '(no submission)' text."""
    solution = _make_parsed("1.1", "Explain entropy.")
    student = _make_parsed("1.1", "Explain entropy.\n\nEntropy measures disorder.")

    messages, _, _ = prompt_builder.build_group_prompt(
        ["1.1"], solution, student, "sys"
    )
    blob = _text_blob(messages)

    submission_start = blob.index("<<<STUDENT_SUBMISSION>>>")
    submission_end = blob.index("<<<END_STUDENT_SUBMISSION>>>")
    submission_block = blob[submission_start:submission_end]

    assert "(no submission)" not in submission_block


def test_build_genai_detection_user_message_none_when_empty():
    from genai_detection import build_genai_detection_user_message

    student = {"sections": {"1": {"questions": {"1.1": {"question_markdown": ""}}}}}
    assert build_genai_detection_user_message(["1.1"], student, 8000) is None
