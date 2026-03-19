"""Tests for parse_notebook.py: parsing logic and helper functions."""

import json
from pathlib import Path

import pytest

from parse_notebook import (
    extract_code_outputs,
    extract_qids_from_notebook,
    get_all_question_ids,
    get_total_points,
    md_text,
    parse_notebook,
)

# ---------------------------------------------------------------------------
# Helper: build minimal notebook dict
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# md_text
# ---------------------------------------------------------------------------


class TestMdText:
    def test_plain(self):
        cell = {"source": ["Hello ", "world"]}
        assert md_text(cell) == "Hello world"

    def test_empty(self):
        assert md_text({"source": []}) == ""


# ---------------------------------------------------------------------------
# extract_code_outputs
# ---------------------------------------------------------------------------


class TestExtractCodeOutputs:
    def test_stream_output(self):
        cell = make_code_cell(
            "print('hi')", [{"output_type": "stream", "text": ["hi\n"]}]
        )
        out = extract_code_outputs(cell)
        assert "hi" in out["output_text"]

    def test_execute_result_text(self):
        cell = make_code_cell(
            "42",
            [
                {
                    "output_type": "execute_result",
                    "data": {"text/plain": ["42"]},
                }
            ],
        )
        out = extract_code_outputs(cell)
        assert "42" in out["output_text"]

    def test_image_captured(self):
        cell = make_code_cell(
            "plt.show()",
            [
                {
                    "output_type": "display_data",
                    "data": {"image/png": "abc123=="},
                }
            ],
        )
        out = extract_code_outputs(cell, keep_images_base64=True)
        assert len(out["images"]) == 1
        assert out["images"][0]["base64"] == "abc123=="

    def test_image_suppressed_when_disabled(self):
        cell = make_code_cell(
            "plt.show()",
            [
                {
                    "output_type": "display_data",
                    "data": {"image/png": "abc123=="},
                }
            ],
        )
        out = extract_code_outputs(cell, keep_images_base64=False)
        assert out["images"] == []

    def test_error_output(self):
        cell = make_code_cell(
            "1/0",
            [
                {
                    "output_type": "error",
                    "traceback": ["ZeroDivisionError: division by zero"],
                }
            ],
        )
        out = extract_code_outputs(cell)
        assert "ZeroDivisionError" in out["output_text"]


# ---------------------------------------------------------------------------
# extract_qids_from_notebook (one match per cell, same as parse_notebook)
# ---------------------------------------------------------------------------

QUESTION_REGEX = r"(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]"


class TestExtractQidsFromNotebook:
    def test_one_match_per_cell_q1_1_twice_in_same_cell_not_duplicate(self):
        """Q1.1 appearing twice in one cell = one match, NOT duplicate."""
        cells = [
            {
                "cell_type": "markdown",
                "source": ["- Q1.1 [2 PTS] First\n- Q1.1 [2 PTS] Again"],
            },
        ]
        found, dupes = extract_qids_from_notebook(cells, QUESTION_REGEX)
        assert found == ["1.1"]
        assert dupes == []

    def test_q1_1_in_two_cells_is_duplicate(self):
        """Q1.1 in two different cells = duplicate."""
        cells = [
            {"cell_type": "markdown", "source": ["- Q1.1 [2 PTS] First"]},
            {"cell_type": "markdown", "source": ["- Q1.1 [2 PTS] Duplicate"]},
        ]
        found, dupes = extract_qids_from_notebook(cells, QUESTION_REGEX)
        assert found == ["1.1"]
        assert dupes == ["1.1"]

    def test_multiple_questions_no_duplicates(self):
        cells = [
            {"cell_type": "markdown", "source": ["- Q1.1 [2 PTS] One"]},
            {"cell_type": "markdown", "source": ["- Q1.2 [3 PTS] Two"]},
        ]
        found, dupes = extract_qids_from_notebook(cells, QUESTION_REGEX)
        assert found == ["1.1", "1.2"]
        assert dupes == []


# ---------------------------------------------------------------------------
# parse_notebook (integration-style, no file I/O)
# ---------------------------------------------------------------------------

CONFIG = {
    "parsing": {
        "section_regex": r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
        "question_regex": r"(?i)^\s*-\s*Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
        "keep_images": True,
    }
}


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
        assert qs["1.1"]["points"] == 2
        assert "print('hello world')" in qs["1.1"]["answer_code_concat"]

    def test_markdown_answer_captured(self, tmp_path):
        cells = [
            make_md_cell("# <font color='red'>2 Theory</font>"),
            make_md_cell(
                "- Q2.1 <font color='blue'>[1 PTS] Explain gradient descent</font>"
            ),
            make_md_cell("Gradient descent minimizes loss iteratively."),
        ]
        result = self._write_and_parse(cells, tmp_path)
        q = result["sections"]["2"]["questions"]["2.1"]
        assert "gradient descent" in q["answer_markdown_concat"].lower()

    def test_multiple_code_cells_concatenated(self, tmp_path):
        cells = [
            make_md_cell("# <font color='red'>3 Code</font>"),
            make_md_cell("- Q3.1 <font color='blue'>[3 PTS] Do stuff</font>"),
            make_code_cell("import numpy as np"),
            make_code_cell("x = np.array([1, 2, 3])"),
        ]
        result = self._write_and_parse(cells, tmp_path)
        concat = result["sections"]["3"]["questions"]["3.1"]["answer_code_concat"]
        assert "import numpy" in concat
        assert "np.array" in concat

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
        assert "code_for_1_2" in q2

    def test_markdown_subheaders_in_answer_not_treated_as_section(self, tmp_path):
        """Student answers with ### 1. Foo (plain markdown, no HTML) should be
        captured, not mistaken for handout section headers."""
        config = {
            "parsing": {
                "section_regex": r"(?m)^\s*#+\s*(?:<[^>]+>\s*)*(\d+)\.(?!\d)",
                "question_regex": r"(?mi)^\s*#+.*?Q(\d+)\.(\d+).*?\[\s*(\d+)\s*PTS\s*\]",
                "keep_images": True,
            }
        }
        cells = [
            make_md_cell("# <font color='orange'> 2. Model comparison</font>"),
            make_md_cell("# <font color='blue'> Q2.1 [5 PTS] Compare models</font>"),
            make_md_cell(
                "### 1. Model Results\n"
                "I tested the models. Here are the scores:\n"
                "* **Linear Regression:** 0.650\n"
                "* **k-NN:** 0.890\n\n"
                "### 2. Final Selection\n"
                "I chose Linear Regression."
            ),
        ]
        nb = make_notebook(cells)
        p = tmp_path / "test.ipynb"
        p.write_text(json.dumps(nb), encoding="utf-8")
        result = parse_notebook(p, config)
        q = result["sections"]["2"]["questions"]["2.1"]
        md = q["answer_markdown_concat"]
        assert "### 1. Model Results" in md
        assert "### 2. Final Selection" in md
        assert "I chose Linear Regression" in md

    def test_q1_21_header_not_matched_as_section(self, tmp_path):
        """Q1.21 header (# <font...> Q1.21 Report [4 PTS]) must be parsed as a
        question, not mistaken for section 1 due to '1.' in Q1.21."""
        config = {
            "parsing": {
                "section_regex": r"(?m)^\s*#+\s*(?:<[^>]+>\s*)*(\d+)\.(?!\d)",
                "question_regex": r"(?mi)^\s*#+.*?Q(\d+)\.(\d+).*?\[\s*(\d+)\s*PTS\s*\]",
                "keep_images": True,
            }
        }
        cells = [
            make_md_cell("# <center><font color='orange'> 1. ML pipeline</center>"),
            make_md_cell(
                "# <font color='blue'> Q1.21 Report to the stakeholder [4 PTS] </font>\n"
                "Provide a brief summary..."
            ),
            make_md_cell(
                "### 1. Summary of the Chosen Model\n"
                "The final model is k-NN with k=1."
            ),
        ]
        nb = make_notebook(cells)
        p = tmp_path / "test.ipynb"
        p.write_text(json.dumps(nb), encoding="utf-8")
        result = parse_notebook(p, config)
        assert "1.21" in result["sections"]["1"]["questions"]
        q = result["sections"]["1"]["questions"]["1.21"]
        assert "### 1. Summary of the Chosen Model" in q["answer_markdown_concat"]
        assert "k-NN with k=1" in q["answer_markdown_concat"]


# ---------------------------------------------------------------------------
# get_all_question_ids
# ---------------------------------------------------------------------------


class TestGetAllQuestionIds:
    def _make_parsed(self, qids: list[str]) -> dict:
        sections: dict = {}
        for qid in qids:
            parts = qid.split(".")
            sec = parts[0] if parts else "0"
            sections.setdefault(sec, {"questions": {}})
            sections[sec]["questions"][qid] = {"points": 1}
        return {"sections": sections}

    def test_returns_sorted(self):
        parsed = self._make_parsed(["2.1", "1.3", "1.1", "1.2"])
        ids = get_all_question_ids(parsed)
        assert ids == ["1.1", "1.2", "1.3", "2.1"]

    def test_empty(self):
        assert get_all_question_ids({"sections": {}}) == []

    def test_non_standard_qid_no_crash(self):
        parsed = self._make_parsed(["1.1", "foo", "2.1"])
        ids = get_all_question_ids(parsed)
        assert "1.1" in ids
        assert "2.1" in ids
        assert "foo" in ids
        assert ids.index("1.1") < ids.index("2.1") < ids.index("foo")


# ---------------------------------------------------------------------------
# get_total_points
# ---------------------------------------------------------------------------


class TestGetTotalPoints:
    def test_sum(self):
        parsed = {
            "sections": {
                "1": {"questions": {"1.1": {"points": 2}, "1.2": {"points": 3}}},
                "2": {"questions": {"2.1": {"points": 5}}},
            }
        }
        assert get_total_points(parsed) == 10
