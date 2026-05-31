"""Tests for question-type tag extraction and prompt injection."""

import json
from pathlib import Path

from config_models import DEFAULT_MODEL, ensure_app_config
from parse_notebook import parse_notebook
from prompt_builder import build_group_prompt
from tag_notebook import tag_notebook


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
    def test_extracts_type_tag(self, tmp_path):
        nb = _make_notebook(
            [_question_cell("1.1", tags=["type:analysis"]), _code_cell()]
        )
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(nb))
        cfg = ensure_app_config(
            {"model": DEFAULT_MODEL, "grading": {"question_groups": []}}
        )
        parsed = parse_notebook(nb_path, cfg)
        assert (
            parsed["sections"]["1"]["questions"]["1.1"]["question_type"] == "analysis"
        )


class TestTagNotebookScript:
    def test_injects_tags(self, tmp_path):
        nb = _make_notebook([_question_cell("1.1"), _question_cell("1.2")])
        nb_path = tmp_path / "test.ipynb"
        nb_path.write_text(json.dumps(nb))

        tagged = tag_notebook(nb_path, {"1.1": "code", "1.2": "analysis"})
        assert tagged == {"1.1": "code", "1.2": "analysis"}

        updated = json.loads(nb_path.read_text())
        assert "type:code" in updated["cells"][0]["metadata"]["tags"]


class TestPromptTypeInjection:
    def test_type_instruction_injected_for_tagged_question(self):
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

        messages, _, _ = build_group_prompt(["1.1"], sol, stu, "system prompt")
        user_content = messages[1]["content"]
        text_parts = [p["text"] for p in user_content if p.get("type") == "input_text"]
        full_text = " ".join(text_parts)
        assert "open-ended" in full_text.lower() or "Open-ended" in full_text
