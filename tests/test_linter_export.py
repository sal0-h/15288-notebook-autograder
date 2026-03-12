"""Tests for linter_export.py: Phase 1 linter autograder zip."""

import json
import zipfile
from pathlib import Path

import pytest
import yaml

from linter_export import export_linter_zip


def _make_solution_notebook(tmp_path: Path) -> Path:
    """Create a minimal solution notebook with Q1.1 and Q1.2."""
    nb = {
        "cells": [
            {
                "cell_type": "markdown",
                "source": ["# <font color='blue'>1</font>\n", "\n", "Section 1"],
            },
            {
                "cell_type": "markdown",
                "source": ["- Q1.1 [2 PTS] First question"],
            },
            {
                "cell_type": "markdown",
                "source": ["- Q1.2 [3 PTS] Second question"],
            },
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 4,
    }
    path = tmp_path / "solution.ipynb"
    path.write_text(json.dumps(nb, indent=2), encoding="utf-8")
    return path


class TestLinterExport:
    def test_creates_zip_with_required_files(self, tmp_path):
        sol_path = _make_solution_notebook(tmp_path)
        out_dir = tmp_path / "output" / "Test"
        out_dir.mkdir(parents=True)
        config_path = out_dir / "config.yaml"
        config_path.write_text(
            yaml.dump({
                "output_dir": str(out_dir),
                "assignment_name": "Test",
                "solution_notebook": str(sol_path),
                "parsing": {
                    "question_regex": r"(?i)^\s*(-\s*)?Q(\d+)\.(\d+)\s*.*?\[\s*(\d+)\s*PTS\s*\]",
                },
            }),
            encoding="utf-8",
        )
        zip_path = export_linter_zip(config_path)
        assert zip_path.exists()
        assert zip_path.name == "linter_autograder.zip"
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = zf.namelist()
            assert "setup.sh" in names
            assert "run_autograder" in names
            for name in ("setup.sh", "run_autograder"):
                info = zf.getinfo(name)
                assert info.create_system == 3
                assert (info.external_attr >> 16) & 0o111
            script = zf.read("run_autograder").decode()
            assert "1.1" in script
            assert "1.2" in script

    def test_raises_if_solution_missing(self, tmp_path):
        out_dir = tmp_path / "output" / "Test"
        out_dir.mkdir(parents=True)
        config_path = out_dir / "config.yaml"
        config_path.write_text(
            yaml.dump({
                "output_dir": str(out_dir),
                "assignment_name": "Test",
                "solution_notebook": str(tmp_path / "nonexistent.ipynb"),
            }),
            encoding="utf-8",
        )
        with pytest.raises(FileNotFoundError, match="Solution notebook not found"):
            export_linter_zip(config_path)
