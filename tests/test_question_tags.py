"""Tests for question-type tag extraction, prompt injection, and tag_notebook script."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config_models import DEFAULT_MODEL, ensure_app_config
from parse_notebook import parse_notebook, VALID_QUESTION_TYPES
from tag_notebook import tag_notebook, VALID_QUESTION_TYPES as SCRIPT_TYPES


def _make_notebook(cells: list[dict]) -> dict:
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": {}, "cells": cells}


def _question_cell(qid: str, pts: int = 5, tags: list[str] | None = None) -> dict:
    cell = {
        "cell_type": "markdown",
        "metadata": {},
        "source": [f"Q{qid} Question text [{pts} PTS]"],
    }
    if tags:
        cell["metadata"]["tags"] = tags
    return cell


def _code_cell(code: str = "x = 1") -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "source": [code],
        "outputs": [],
        "execution_count": 1,
    }


class TestParserTagExtraction:
    """Test that parse_notebook extracts question_type from cell metadata tags."""

    def _parse(self, cells, tmp_path):
        nb = _make_notebook(cells)
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(nb))
        cfg = ensure_app_config(
            {"model": DEFAULT_MODEL, "grading": {"question_groups": []}}
        )
        return parse_notebook(nb_path, cfg)

    def test_extracts_type_tag(self, tmp_path):
        cells = [_question_cell("1.1", tags=["type:analysis"]), _code_cell()]
        parsed = self._parse(cells, tmp_path)
        q = parsed["sections"]["1"]["questions"]["1.1"]
        assert q["question_type"] == "analysis"

    def test_defaults_to_mixed_without_tag(self, tmp_path):
        cells = [_question_cell("1.1"), _code_cell()]
        parsed = self._parse(cells, tmp_path)
        q = parsed["sections"]["1"]["questions"]["1.1"]
        assert q["question_type"] == "mixed"

    def test_ignores_unknown_type(self, tmp_path):
        cells = [_question_cell("1.1", tags=["type:bogus"]), _code_cell()]
        parsed = self._parse(cells, tmp_path)
        q = parsed["sections"]["1"]["questions"]["1.1"]
        assert q["question_type"] == "mixed"

    def test_all_valid_types(self, tmp_path):
        for qtype in VALID_QUESTION_TYPES:
            cells = [_question_cell("1.1", tags=[f"type:{qtype}"]), _code_cell()]
            parsed = self._parse(cells, tmp_path)
            q = parsed["sections"]["1"]["questions"]["1.1"]
            assert q["question_type"] == qtype

    def test_non_type_tags_ignored(self, tmp_path):
        cells = [_question_cell("1.1", tags=["group:1", "priority:high"]), _code_cell()]
        parsed = self._parse(cells, tmp_path)
        q = parsed["sections"]["1"]["questions"]["1.1"]
        assert q["question_type"] == "mixed"


class TestTagNotebookScript:
    """Test the tag_notebook.py injection script."""

    def test_injects_tags(self, tmp_path):
        nb = _make_notebook([_question_cell("1.1"), _question_cell("1.2")])
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(nb))

        tagged = tag_notebook(nb_path, {"1.1": "code", "1.2": "analysis"})
        assert tagged == {"1.1": "code", "1.2": "analysis"}

        # Verify written to disk
        updated = json.loads(nb_path.read_text())
        tags_1 = updated["cells"][0]["metadata"]["tags"]
        assert "type:code" in tags_1

    def test_dry_run_does_not_write(self, tmp_path):
        nb = _make_notebook([_question_cell("1.1")])
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(nb))
        original = nb_path.read_text()

        tag_notebook(nb_path, {"1.1": "plot"}, dry_run=True)
        assert nb_path.read_text() == original

    def test_replaces_existing_type_tag(self, tmp_path):
        nb = _make_notebook([_question_cell("1.1", tags=["type:code", "other:tag"])])
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(nb))

        tag_notebook(nb_path, {"1.1": "analysis"})
        updated = json.loads(nb_path.read_text())
        tags = updated["cells"][0]["metadata"]["tags"]
        assert "type:analysis" in tags
        assert "type:code" not in tags
        assert "other:tag" in tags

    def test_skips_unknown_type(self, tmp_path):
        nb = _make_notebook([_question_cell("1.1")])
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(nb))

        tagged = tag_notebook(nb_path, {"1.1": "nonsense"})
        assert tagged == {}


class TestPromptTypeInjection:
    """Test that build_group_prompt includes question type instructions."""

    def test_type_instruction_injected_for_tagged_question(self):
        from prompt_builder import build_group_prompt

        sol = {
            "sections": {
                "1": {
                    "questions": {
                        "1.1": {
                            "points": 5,
                            "question_markdown": "Q1.1",
                            "question_type": "open-ended",
                            "answer_code_concat": "",
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
                            "points": 5,
                            "question_markdown": "Q1.1",
                            "question_type": "open-ended",
                            "answer_code_concat": "x=1",
                            "answer_text_concat": "",
                            "answer_markdown_concat": "",
                            "answer_cells": [],
                        }
                    }
                }
            }
        }

        messages, _ = build_group_prompt(["1.1"], sol, stu, "system prompt")
        # The user message content should contain the open-ended instruction
        user_content = messages[1]["content"]
        text_parts = [p["text"] for p in user_content if p.get("type") == "input_text"]
        full_text = " ".join(text_parts)
        assert "open-ended" in full_text.lower() or "Open-ended" in full_text

    def test_mixed_type_no_extra_instruction(self):
        from prompt_builder import build_group_prompt

        sol = {
            "sections": {
                "1": {
                    "questions": {
                        "1.1": {
                            "points": 5,
                            "question_markdown": "Q1.1",
                            "question_type": "mixed",
                            "answer_code_concat": "",
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
                            "points": 5,
                            "question_markdown": "Q1.1",
                            "question_type": "mixed",
                            "answer_code_concat": "x=1",
                            "answer_text_concat": "",
                            "answer_markdown_concat": "",
                            "answer_cells": [],
                        }
                    }
                }
            }
        }

        messages, _ = build_group_prompt(["1.1"], sol, stu, "system prompt")
        user_content = messages[1]["content"]
        text_parts = [p["text"] for p in user_content if p.get("type") == "input_text"]
        full_text = " ".join(text_parts)
        assert "QUESTION TYPE:" not in full_text
