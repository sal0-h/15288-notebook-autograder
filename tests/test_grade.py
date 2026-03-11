"""Tests for grade.py: Pydantic validation, JSON parsing, and prompt building."""

import json
from pathlib import Path

import pytest

from grade import (
    GradingResponse,
    QuestionGrade,
    build_group_prompt,
    estimate_tokens,
    parse_llm_json,
    truncate_output,
    validate_question_groups,
)


# ---------------------------------------------------------------------------
# QuestionGrade validation
# ---------------------------------------------------------------------------

class TestQuestionGrade:
    def test_normal(self):
        q = QuestionGrade(score=3.0, feedback="Good work")
        assert q.score == 3.0
        assert q.feedback == "Good work"

    def test_coerce_score_from_string(self):
        q = QuestionGrade(score="2.5", feedback="")
        assert q.score == 2.5

    def test_coerce_score_invalid(self):
        q = QuestionGrade(score="abc", feedback="")
        assert q.score == 0.0

    def test_coerce_feedback_none(self):
        q = QuestionGrade(score=1, feedback=None)
        assert q.feedback == ""

    def test_coerce_feedback_number(self):
        q = QuestionGrade(score=1, feedback=42)
        assert q.feedback == "42"


# ---------------------------------------------------------------------------
# GradingResponse.from_raw
# ---------------------------------------------------------------------------

class TestGradingResponseFromRaw:
    def test_exact_keys(self):
        raw = {"4.1": {"score": 2, "feedback": "ok"}, "4.2": {"score": 0, "feedback": "wrong"}}
        gr = GradingResponse.from_raw(raw, ["4.1", "4.2"])
        assert gr.grades["4.1"].score == 2.0
        assert gr.grades["4.2"].feedback == "wrong"

    def test_normalize_Q_prefix(self):
        raw = {"Q4.1": {"score": 1, "feedback": "fine"}}
        gr = GradingResponse.from_raw(raw, ["4.1"])
        assert "4.1" in gr.grades
        assert gr.grades["4.1"].score == 1.0

    def test_normalize_lowercase_q(self):
        raw = {"q9.3": {"score": 3, "feedback": "great"}}
        gr = GradingResponse.from_raw(raw, ["9.3"])
        assert gr.grades["9.3"].score == 3.0

    def test_missing_expected_qid_filled_with_zero(self):
        raw = {"4.1": {"score": 2, "feedback": "ok"}}
        gr = GradingResponse.from_raw(raw, ["4.1", "4.2"])
        assert "4.2" in gr.grades
        assert gr.grades["4.2"].score == 0.0

    def test_numeric_value_coerced(self):
        raw = {"4.1": 3}
        gr = GradingResponse.from_raw(raw, ["4.1"])
        assert gr.grades["4.1"].score == 3.0
        assert gr.grades["4.1"].feedback == ""

    def test_malformed_value_falls_back_to_zero(self):
        raw = {"4.1": "garbage"}
        gr = GradingResponse.from_raw(raw, ["4.1"])
        assert gr.grades["4.1"].score == 0.0


# ---------------------------------------------------------------------------
# parse_llm_json
# ---------------------------------------------------------------------------

class TestParseLlmJson:
    def test_plain_json(self):
        text = '{"4.1": {"score": 2, "feedback": "ok"}}'
        result = parse_llm_json(text)
        assert result["4.1"]["score"] == 2

    def test_json_in_code_fence(self):
        text = '```json\n{"4.1": {"score": 1}}\n```'
        result = parse_llm_json(text)
        assert result["4.1"]["score"] == 1

    def test_json_embedded_in_prose(self):
        text = 'Here is my evaluation:\n\n{"4.1": {"score": 3, "feedback": "good"}}\n\nDone.'
        result = parse_llm_json(text)
        assert result["4.1"]["score"] == 3

    def test_empty_response(self):
        assert parse_llm_json("") == {}

    def test_invalid_json(self):
        assert parse_llm_json("This is not JSON at all.") == {}


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_text_only(self):
        tokens = estimate_tokens("a" * 350, 0)
        # With tiktoken: ~45; with chars/3.5 fallback: 100
        assert tokens >= 40 and tokens <= 150

    def test_with_images(self):
        tokens = estimate_tokens("", 2)
        assert tokens == 2000  # 2 * 1000


# ---------------------------------------------------------------------------
# truncate_output
# ---------------------------------------------------------------------------

class TestTruncateOutput:
    def test_no_truncation_needed(self):
        text = "short"
        assert truncate_output(text, 100) == "short"

    def test_truncation_applied(self):
        text = "a" * 200
        result = truncate_output(text, 100)
        assert "truncated" in result
        assert len(result) < 200

    def test_truncation_keeps_start_and_end(self):
        text = "START" + "x" * 200 + "END"
        result = truncate_output(text, 50)
        assert "START" in result
        assert "END" in result


# ---------------------------------------------------------------------------
# validate_question_groups
# ---------------------------------------------------------------------------

class TestValidateQuestionGroups:
    def _make_solution(self, qids: list[str]) -> dict:
        sections: dict = {}
        for qid in qids:
            sec, _ = qid.split(".")
            sections.setdefault(sec, {"questions": {}})
            sections[sec]["questions"][qid] = {"points": 1}
        return {"sections": sections}

    def test_all_covered(self):
        sol = self._make_solution(["1.1", "1.2", "2.1"])
        groups = [["1.1", "1.2"], ["2.1"]]
        ungrouped = validate_question_groups(groups, sol)
        assert ungrouped == []

    def test_missing_question(self):
        sol = self._make_solution(["1.1", "1.2", "2.1"])
        groups = [["1.1", "1.2"]]  # 2.1 not included
        ungrouped = validate_question_groups(groups, sol)
        assert "2.1" in ungrouped

    def test_empty_groups(self):
        sol = self._make_solution(["1.1"])
        ungrouped = validate_question_groups([], sol)
        assert "1.1" in ungrouped


# ---------------------------------------------------------------------------
# build_group_prompt (structure, no API call)
# ---------------------------------------------------------------------------

class TestBuildGroupPrompt:
    def _minimal_parsed(self, qid: str, code: str = "") -> dict:
        sec, qnum = qid.split(".")
        return {"sections": {sec: {"questions": {qid: {
            "points": 2,
            "question_markdown": f"Q{qid} test question",
            "answer_cells": [],
            "answer_code_concat": code,
            "answer_text_concat": "",
            "answer_markdown_concat": "",
        }}}}}

    def test_returns_two_messages(self):
        sol = self._minimal_parsed("4.1", "print('ref')")
        stu = self._minimal_parsed("4.1", "print('stu')")
        messages, qid_to_max = build_group_prompt(["4.1"], sol, stu, "You grade.")
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_qid_to_max_populated(self):
        sol = self._minimal_parsed("4.1")
        stu = self._minimal_parsed("4.1")
        _, qid_to_max = build_group_prompt(["4.1"], sol, stu, "You grade.")
        assert qid_to_max["4.1"] == 2

    def test_content_parts_include_text(self):
        sol = self._minimal_parsed("4.1", "solution_code")
        stu = self._minimal_parsed("4.1", "student_code")
        messages, _ = build_group_prompt(["4.1"], sol, stu, "You grade.")
        content = messages[1]["content"]
        all_text = " ".join(p["text"] for p in content if p["type"] == "text")
        assert "REFERENCE SOLUTION" in all_text
        assert "STUDENT SUBMISSION" in all_text
        assert "solution_code" in all_text
        assert "student_code" in all_text

    def test_no_answer_shows_placeholder(self):
        sol = self._minimal_parsed("4.1")
        stu = {"sections": {}}  # student has nothing
        messages, _ = build_group_prompt(["4.1"], sol, stu, "You grade.")
        content = messages[1]["content"]
        all_text = " ".join(p["text"] for p in content if p["type"] == "text")
        assert "not found in student submission" in all_text
