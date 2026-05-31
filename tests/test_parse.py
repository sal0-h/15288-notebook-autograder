"""Tests for parse_notebook.py: parsing logic and helper functions."""

import json
from pathlib import Path

from config_models import ensure_app_config
from parse_notebook import (
    extract_code_outputs,
    get_all_question_ids,
    get_total_points,
    parse_notebook,
)


def make_notebook(cells: list[dict]) -> dict:
    return {"cells": cells, "nbformat": 4, "metadata": {}}


def make_md_cell(source: str) -> dict:
    return {"cell_type": "markdown", "source": [source], "metadata": {}}


def make_code_cell(source: str, outputs: list[dict] | None = None) -> dict:
    return {
        "cell_type": "code",
        "source": [source],
        "outputs": outputs or [],
        "metadata": {},
    }


class TestExtractCodeOutputs:
    def test_stream_output(self):
        cell = make_code_cell(
            "print('hi')", [{"output_type": "stream", "text": ["hi\n"]}]
        )
        out = extract_code_outputs(cell)
        assert "hi" in out["output_text"]

    def test_image_captured(self):
        cell = make_code_cell(
            "plt.show()",
            [{"output_type": "display_data", "data": {"image/png": "abc123=="}}],
        )
        out = extract_code_outputs(cell, keep_images_base64=True)
        assert len(out["images"]) == 1


CONFIG = ensure_app_config(
    {
        "parsing": {
            "section_regex": r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
            "question_regex": r"(?i)^\s*-\s*Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
            "keep_images": True,
        }
    }
)


class TestParseNotebook:
    def _write_and_parse(self, cells: list[dict], tmp_path: Path) -> dict:
        nb = make_notebook(cells)
        p = tmp_path / "test.ipynb"
        p.write_text(json.dumps(nb), encoding="utf-8")
        return parse_notebook(p, CONFIG)

    def test_section_detected(self, tmp_path):
        cells = [make_md_cell("# <font color='red'>1 Introduction</font>")]
        result = self._write_and_parse(cells, tmp_path)
        assert "1" in result["sections"]

    def test_question_parsed(self, tmp_path):
        cells = [
            make_md_cell("# <font color='red'>1 Intro</font>"),
            make_md_cell("- Q1.1 <font color='blue'>[2 PTS] Write hello world</font>"),
            make_code_cell("print('hello world')"),
        ]
        result = self._write_and_parse(cells, tmp_path)
        qs = result["sections"]["1"]["questions"]
        assert "1.1" in qs
        assert "print('hello world')" in qs["1.1"]["answer_code_concat"]

    def test_next_question_stops_collection(self, tmp_path):
        cells = [
            make_md_cell("# <font color='red'>1 Section</font>"),
            make_md_cell("- Q1.1 <font color='blue'>[1 PTS] First</font>"),
            make_code_cell("code_for_1_1 = True"),
            make_md_cell("- Q1.2 <font color='blue'>[1 PTS] Second</font>"),
            make_code_cell("code_for_1_2 = True"),
        ]
        result = self._write_and_parse(cells, tmp_path)
        q1 = result["sections"]["1"]["questions"]["1.1"]["answer_code_concat"]
        q2 = result["sections"]["1"]["questions"]["1.2"]["answer_code_concat"]
        assert "code_for_1_1" in q1
        assert "code_for_1_1" not in q2

    def test_markdown_subheaders_in_answer_not_treated_as_section(self, tmp_path):
        config = {
            "parsing": {
                "section_regex": r"(?m)^\s*#\s+<font[^>]*>\s*(\d+)\.(?!\d)",
                "question_regex": r"(?mi)^\s*#+.*?Q(\d+)\.(\d+).*?\[\s*(\d+)\s*PTS\s*\]",
                "keep_images": True,
            }
        }
        cells = [
            make_md_cell("# <font color='orange'> 2. Model comparison</font>"),
            make_md_cell("# <font color='blue'> Q2.1 [5 PTS] Compare models</font>"),
            make_md_cell(
                "### 1. Model Results\n"
                "I tested the models.\n\n"
                "### 2. Final Selection\n"
                "I chose Linear Regression."
            ),
        ]
        nb = make_notebook(cells)
        p = tmp_path / "test.ipynb"
        p.write_text(json.dumps(nb), encoding="utf-8")
        result = parse_notebook(p, ensure_app_config(config))
        md = result["sections"]["2"]["questions"]["2.1"]["answer_markdown_concat"]
        assert "### 1. Model Results" in md
        assert "I chose Linear Regression" in md


class TestGetAllQuestionIds:
    def test_returns_sorted(self):
        parsed = {
            "sections": {
                "2": {"questions": {"2.1": {"points": 1}}},
                "1": {
                    "questions": {
                        "1.3": {"points": 1},
                        "1.1": {"points": 1},
                        "1.2": {"points": 1},
                    }
                },
            }
        }
        assert get_all_question_ids(parsed) == ["1.1", "1.2", "1.3", "2.1"]


class TestGetTotalPoints:
    def test_sum(self):
        parsed = {
            "sections": {
                "1": {"questions": {"1.1": {"points": 2}, "1.2": {"points": 3}}},
                "2": {"questions": {"2.1": {"points": 5}}},
            }
        }
        assert get_total_points(parsed) == 10
