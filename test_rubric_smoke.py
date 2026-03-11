#!/usr/bin/env python3
"""Smoke test for parallel rubric generation."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Fix solution structure: sections = {sec_id: {questions: {qid: {...}}}}
sol = {
    "sections": {
        "4": {
            "questions": {
                "4.1": {
                    "points": 2,
                    "question_markdown": "Q4.1",
                    "answer_code_concat": "x=1",
                    "answer_text_concat": "",
                    "answer_markdown_concat": "",
                }
            },
        },
        "5": {
            "questions": {
                "5.1": {
                    "points": 2,
                    "question_markdown": "Q5.1",
                    "answer_code_concat": "y=2",
                    "answer_text_concat": "",
                    "answer_markdown_concat": "",
                }
            },
        },
    }
}

tmp = Path("/tmp/rubric_test")
tmp.mkdir(exist_ok=True)
(tmp / "solution_parsed.json").write_text(json.dumps(sol))

config = {
    "output_dir": str(tmp),
    "grading": {"question_groups": [["4.1"], ["5.1"]]},
    "model": "gpt-4o",
    "max_completion_tokens": 4096,
    "workers": 2,
}

mock_response = MagicMock()
mock_response.choices = [MagicMock()]
mock_response.choices[0].message.content = json.dumps({
    "4.1": {"points": 2, "items": [{"description": "a", "deduction": 2.0}]},
    "5.1": {"points": 2, "items": [{"description": "b", "deduction": 2.0}]},
})

mock_client = MagicMock()
mock_client.chat.completions.create.return_value = mock_response


def run_test():
    with patch("rubric.get_openai_client", return_value=mock_client):
        from rubric import generate_rubrics

        r = generate_rubrics(config)
        assert "4.1" in r, f"Missing 4.1, got {list(r.keys())}"
        assert "5.1" in r, f"Missing 5.1, got {list(r.keys())}"
        print("Rubrics:", list(r.keys()))
        print("OK (parallel with workers=2)")


if __name__ == "__main__":
    run_test()
    sys.exit(0)
