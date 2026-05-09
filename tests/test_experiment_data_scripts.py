"""Tests for experiment_data layout build + prepare scripts (no real course data)."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import yaml

import pytest

import parse_notebook
from config_models import ensure_app_config


def _load_script(name: str):
    root = Path(__file__).resolve().parent.parent
    path = root / "scripts" / name
    spec = importlib.util.spec_from_file_location(
        name.replace(".py", "").replace("-", "_"), path
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Question column parsing
# ---------------------------------------------------------------------------


def test_parse_question_columns_natural():
    build = _load_script("build_experiment_layout.py")
    headers = [
        "anon_id",
        "Total Score",
        "Max Points",
        "1: Autograder (1.0 pts)",
        "2.1: Question 1 (2.0 pts)",
        "2.2: Question 2 (1.0 pts)",
        "3.1: Question 1 (3.0 pts)",
    ]
    out = build._parse_question_columns(headers)
    assert [(s.qid, s.pts) for s, _ in out] == [
        ("2.1", 2),
        ("2.2", 1),
        ("3.1", 3),
    ]


def test_parse_question_columns_dual_uses_notebook_qid():
    build = _load_script("build_experiment_layout.py")
    headers = [
        "anon_id",
        "Total Score",
        "Max Points",
        "3.4: 2.4 (12.0 pts)",
        "4.1: 3.1 (7.0 pts)",
    ]
    assert build.gradescope_notebook_dual_headers_present(headers)
    out = build._parse_question_columns(headers)
    assert [(s.qid, s.pts) for s, _ in out] == [("2.4", 12), ("3.1", 7)]


def test_remap_gradescope_notebook_qid_in_header():
    remap = _load_script("remap_gradescope_notebook_qids.py")
    mapping = {"5.1": "9.1"}
    old = "6.1: 5.1 (10.0 pts)"
    new, changed = remap.remap_gradescope_header(old, mapping)
    assert changed
    assert new == "6.1: 9.1 (10.0 pts)"
    _, again = remap.remap_gradescope_header(new, mapping)
    assert not again


def test_parse_question_columns_synthesizes_when_inconsistent():
    build = _load_script("build_experiment_layout.py")
    headers = [
        "anon_id",
        "1: Autograder (0.0 pts)",
        "2: q1 (3.0 pts)",
        "3: q2 (2.0 pts)",
        "4.1: 10.a (5.0 pts)",
    ]
    out = build._parse_question_columns(headers)
    assert [s.qid for s, _ in out] == ["1.1", "1.2", "1.3"]


# ---------------------------------------------------------------------------
# S25 notebook rewriter
# ---------------------------------------------------------------------------


def _md_cell(src: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": src.splitlines(keepends=True),
    }


def _make_s25_solution(tmp_path: Path) -> Path:
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            _md_cell("# heading"),
            _md_cell("- 1 <font color='blue'> [2 pts] do thing one"),
            _md_cell("- 2 <font color='blue'> [3 pts] do thing two"),
        ],
    }
    p = tmp_path / "solution_raw.ipynb"
    p.write_text(json.dumps(nb), encoding="utf-8")
    return p


def test_rewrite_s25_notebook_injects_q_headers(tmp_path):
    build = _load_script("build_experiment_layout.py")
    src = _make_s25_solution(tmp_path)
    qspecs = [build.QSpec(1, 1, 2), build.QSpec(1, 2, 3)]
    rewritten = build._rewrite_s25_notebook(src, qspecs)
    sources = ["".join(c["source"]) for c in rewritten["cells"]]
    assert sources[1].startswith("Q1.1 [2 PTS]\n")
    assert sources[2].startswith("Q1.2 [3 PTS]\n")
    assert rewritten["_experiment_data_qheader_rewrites"] == 2


# ---------------------------------------------------------------------------
# End-to-end S25 build + parser smoke
# ---------------------------------------------------------------------------


def _write_csv(path: Path, headers: list[str], rows: list[list[str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerows(rows)


def test_build_s25_lab_e2e(tmp_path):
    build = _load_script("build_experiment_layout.py")
    lab = tmp_path / "LabTest_99"
    sub = lab / "submissions"
    sub.mkdir(parents=True)

    sol = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            _md_cell("- 1 <font color='blue'> [2 pts] q1"),
            _md_cell("- 2 <font color='blue'> [3 pts] q2"),
        ],
    }
    (lab / "LabTest_99_S25_sol.ipynb").write_text(json.dumps(sol), encoding="utf-8")
    for stem in ("001", "002"):
        (sub / f"{stem}.ipynb").write_text(json.dumps(sol), encoding="utf-8")

    _write_csv(
        lab / "grades.csv",
        [
            "anon_id",
            "Total Score",
            "Max Points",
            "1: Autograder (0.0 pts)",
            "2.1: Question 1 (2.0 pts)",
            "2.2: Question 2 (3.0 pts)",
        ],
        [
            ["001", "5", "5", "0", "2", "3"],
            ["002", "3", "5", "0", "1", "2"],
        ],
    )

    summary = build.build_s25_lab(lab)
    assert summary["config_yaml"] == "created"
    assert summary["students"] == 2
    assert summary["questions"] == 2
    assert summary["assignment_name"] == "S25_LabTest_99"

    assert (lab / "config.yaml").is_file()
    assert (lab / "solution.ipynb").is_file()
    assert (lab / "human_grades.csv").is_file()
    assert (lab / "submissions" / "001.ipynb").is_file()
    assert (lab / "_raw").is_dir()

    cfg = yaml.safe_load((lab / "config.yaml").read_text())
    cfg["solution_notebook"] = str((lab / "solution.ipynb").resolve())
    assert cfg["model"] == "gpt-4.1"
    assert cfg["workers"] == 32
    assert cfg["rubric_review"] is False
    qids = parse_notebook.get_all_question_ids(
        parse_notebook.parse_notebook(
            Path(cfg["solution_notebook"]), ensure_app_config(cfg)
        )
    )
    csv_qids = (lab / "human_grades.csv").read_text().splitlines()[0].split(",")[3:]
    assert qids == csv_qids == ["2.1", "2.2"]


def test_build_s24_lab_uses_cohort_tag_in_assignment_name(tmp_path):
    build = _load_script("build_experiment_layout.py")
    lab = tmp_path / "LabTest_97"
    sub = lab / "submissions"
    sub.mkdir(parents=True)
    sol = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            _md_cell("- 1 <font color='blue'> [2 pts] q1"),
            _md_cell("- 2 <font color='blue'> [3 pts] q2"),
        ],
    }
    (lab / "LabTest_97_S24_sol.ipynb").write_text(json.dumps(sol), encoding="utf-8")
    (sub / "001.ipynb").write_text(json.dumps(sol), encoding="utf-8")
    _write_csv(
        lab / "grades.csv",
        [
            "anon_id",
            "Total Score",
            "Max Points",
            "1: Autograder (0.0 pts)",
            "2.1: Question 1 (2.0 pts)",
            "2.2: Question 2 (3.0 pts)",
        ],
        [["001", "5", "5", "0", "2", "3"]],
    )
    summary = build.build_s25_lab(lab, cohort="S24")
    assert summary["assignment_name"] == "S24_LabTest_97"
    cfg = yaml.safe_load((lab / "config.yaml").read_text())
    assert cfg["assignment_name"] == "S24_LabTest_97"


def test_build_s25_lab_preserves_existing_config_yaml(tmp_path):
    build = _load_script("build_experiment_layout.py")
    lab = tmp_path / "LabTest_98"
    sub = lab / "submissions"
    sub.mkdir(parents=True)
    sol = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            _md_cell("- 1 <font color='blue'> [2 pts] q1"),
            _md_cell("- 2 <font color='blue'> [3 pts] q2"),
        ],
    }
    (lab / "LabTest_98_S25_sol.ipynb").write_text(json.dumps(sol), encoding="utf-8")
    for stem in ("001",):
        (sub / f"{stem}.ipynb").write_text(json.dumps(sol), encoding="utf-8")
    _write_csv(
        lab / "grades.csv",
        [
            "anon_id",
            "Total Score",
            "Max Points",
            "1: Autograder (0.0 pts)",
            "2.1: Question 1 (2.0 pts)",
            "2.2: Question 2 (3.0 pts)",
        ],
        [["001", "5", "5", "0", "2", "3"]],
    )
    assert build.build_s25_lab(lab)["config_yaml"] == "created"
    cfg_path = lab / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["model"] = "gpt-5-mini"
    cfg["workers"] = 1
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    assert build.build_s25_lab(lab)["config_yaml"] == "skipped"
    cfg2 = yaml.safe_load(cfg_path.read_text())
    assert cfg2["model"] == "gpt-5-mini"
    assert cfg2["workers"] == 1

    assert (
        build.build_s25_lab(lab, overwrite_config=True)["config_yaml"] == "overwritten"
    )
    cfg3 = yaml.safe_load(cfg_path.read_text())
    assert cfg3["model"] == "gpt-4.1"
    assert cfg3["workers"] == 32


# ---------------------------------------------------------------------------
# End-to-end S26 build (synthetic Gradescope ZIP)
# ---------------------------------------------------------------------------


def test_build_s26_lab_e2e(tmp_path):
    build = _load_script("build_experiment_layout.py")
    s26 = tmp_path / "S26"
    s26.mkdir()

    sol = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            _md_cell("Q1.1 [1 PTS] one"),
            _md_cell("Q1.2 [2 PTS] two"),
        ],
    }
    (s26 / "lt2.ipynb").write_text(json.dumps(sol), encoding="utf-8")

    _write_csv(
        s26 / "lt2.csv",
        [
            "First Name",
            "Last Name",
            "SID",
            "Email",
            "Sections",
            "Total Score",
            "Max Points",
            "Status",
            "Submission ID",
            "Submission Time",
            "Lateness (H:M:S)",
            "View Count",
            "Submission Count",
            "1: Autograder (0.0 pts)",
            "2: 1.1 (1.0 pts)",
            "3: 1.2 (2.0 pts)",
        ],
        [
            [
                "Alpha",
                "A",
                "sid1",
                "alpha@example.com",
                "W",
                "3",
                "3",
                "Graded",
                "100",
                "",
                "",
                "",
                "",
                "0",
                "1",
                "2",
            ],
            [
                "Bravo",
                "B",
                "sid2",
                "bravo@example.com",
                "W",
                "1",
                "3",
                "Graded",
                "200",
                "",
                "",
                "",
                "",
                "0",
                "1",
                "0",
            ],
        ],
    )

    # Gradescope-shaped ZIP with two submission folders
    zip_path = s26 / "lt2.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        meta = {
            "submission_100": {":submitters": [{":name": "Alpha"}]},
            "submission_200": {":submitters": [{":name": "Bravo"}]},
        }
        zf.writestr("assignment_1_export/submission_metadata.yml", yaml.safe_dump(meta))
        zf.writestr("assignment_1_export/submission_100/x.ipynb", json.dumps(sol))
        zf.writestr("assignment_1_export/submission_200/y.ipynb", json.dumps(sol))

    summary = build.build_s26_lab(s26, 2)
    assert summary["config_yaml"] == "created"
    assert summary["students"] == 2
    assert summary["questions"] == 2
    lab = s26 / "LabTest_2"
    assert (lab / "config.yaml").is_file()
    assert (lab / "submissions" / "001.ipynb").is_file()
    assert (lab / "submissions" / "002.ipynb").is_file()
    csv_text = (lab / "human_grades.csv").read_text()
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0] == ["anon_id", "Total Score", "Max Points", "1.1", "1.2"]
    assert rows[1][0] == "001"
    assert rows[2][0] == "002"

    cfg_path = lab / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text())
    cfg["model"] = "custom-model"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    assert build.build_s26_lab(s26, 2)["config_yaml"] == "skipped"
    assert yaml.safe_load(cfg_path.read_text())["model"] == "custom-model"
    assert (
        build.build_s26_lab(s26, 2, overwrite_config=True)["config_yaml"]
        == "overwritten"
    )
    assert yaml.safe_load(cfg_path.read_text())["model"] == "gpt-4.1"


# ---------------------------------------------------------------------------
# prepare_assignment
# ---------------------------------------------------------------------------


def test_prepare_assignment_creates_runtime(tmp_path, monkeypatch):
    build = _load_script("build_experiment_layout.py")
    prepare = _load_script("prepare_assignment.py")

    lab = tmp_path / "LabTest_42"
    sub = lab / "submissions"
    sub.mkdir(parents=True)
    sol = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            _md_cell("- 1 <font color='blue'> [1 pts] q1"),
        ],
    }
    (lab / "LabTest_42_S25_sol.ipynb").write_text(json.dumps(sol), encoding="utf-8")
    (sub / "001.ipynb").write_text(json.dumps(sol), encoding="utf-8")
    _write_csv(
        lab / "grades.csv",
        ["anon_id", "Total Score", "Max Points", "2.1: Question 1 (1.0 pts)"],
        [["001", "1", "1", "1"]],
    )
    build.build_s25_lab(lab)

    monkeypatch.setattr(prepare, "PROJECT_ROOT", tmp_path)
    out = prepare.prepare_assignment(lab, copy=True)
    assert out == tmp_path / "output" / "S25_LabTest_42"
    assert (out / "config.yaml").is_file()
    assert (out / "submissions" / "001.ipynb").is_file()

    cfg = yaml.safe_load((out / "config.yaml").read_text())
    assert cfg["assignment_name"] == "S25_LabTest_42"
    assert Path(cfg["solution_notebook"]).is_file()


def test_prepare_all_stages_each_lab(tmp_path, monkeypatch):
    prepare = _load_script("prepare_assignment.py")
    build = _load_script("build_experiment_layout.py")

    exp = tmp_path / "experiment_data"
    (exp / "S25" / "LabTest_A").mkdir(parents=True)
    (exp / "S25" / "LabTest_B").mkdir(parents=True)
    for lab_name in ("LabTest_A", "LabTest_B"):
        lab = exp / "S25" / lab_name
        sub = lab / "submissions"
        sub.mkdir(parents=True)
        sol = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [_md_cell("- 1 <font color='blue'> [1 pts] q1")],
        }
        (lab / f"{lab_name}_S25_sol.ipynb").write_text(
            json.dumps(sol), encoding="utf-8"
        )
        (sub / "001.ipynb").write_text(json.dumps(sol), encoding="utf-8")
        _write_csv(
            lab / "grades.csv",
            ["anon_id", "Total Score", "Max Points", "2.1: Question 1 (1.0 pts)"],
            [["001", "1", "1", "1"]],
        )
        build.build_s25_lab(lab)

    monkeypatch.setattr(prepare, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(prepare, "EXPERIMENT_ROOT", exp)
    labs = prepare._iter_canonical_labs()
    assert len(labs) == 2
    for lab in labs:
        prepare.prepare_assignment(lab, copy=True)
    assert (tmp_path / "output" / "S25_LabTest_A" / "config.yaml").is_file()
    assert (tmp_path / "output" / "S25_LabTest_B" / "config.yaml").is_file()


# ---------------------------------------------------------------------------
# S24 canonicalize (no notebook rewrite)
# ---------------------------------------------------------------------------


def test_canonicalize_experiment_lab_refuses_large_csv_mismatch(tmp_path):
    canon = _load_script("canonicalize_experiment_lab.py")
    lab = tmp_path / "LabTest_Mis"
    sub = lab / "submissions"
    sub.mkdir(parents=True)
    sol = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            _md_cell("# <font color='purple'>1</font>\n"),
            _md_cell("- 1 <font color='blue'> [2 pts] q1"),
            _md_cell("- 2 <font color='blue'> [3 pts] q2"),
        ],
    }
    sol_json = json.dumps(sol)
    (lab / "LabTest_Mis_S24_sol.ipynb").write_text(sol_json, encoding="utf-8")
    (sub / "001.ipynb").write_text(sol_json, encoding="utf-8")
    headers = [
        "anon_id",
        "Total Score",
        "Max Points",
        "1: Autograder (0.0 pts)",
    ] + [f"2.{i}: Question ({i}.0 pts)" for i in range(1, 11)]
    row = ["001", "5", "50", "0.0"] + ["1.0"] * 10
    _write_csv(lab / "grades.csv", headers, [row])
    with pytest.raises(ValueError, match="refusing to drop"):
        canon.canonicalize(
            lab,
            section_regex=r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
            question_regex=r"(?im)^\s*-?\s*(\d+)\s*<font[^>]*>.*?\[\s*(\d+)\s*pts?\s*\]",
            allow_misaligned_human=False,
        )


def test_canonicalize_dual_header_maps_by_notebook_qid_not_position(tmp_path):
    """Gradescope ``<g>: <n> (pts)`` columns align to parser QIDs by notebook id."""
    canon = _load_script("canonicalize_experiment_lab.py")
    lab = tmp_path / "LabTest_Dual"
    sub = lab / "submissions"
    sub.mkdir(parents=True)
    sol = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            _md_cell("# <font color='purple'>1</font>\n"),
            _md_cell("- 1 <font color='blue'> [2 pts] q1"),
            _md_cell("- 2 <font color='blue'> [3 pts] q2"),
        ],
    }
    sol_json = json.dumps(sol)
    (lab / "LabTest_Dual_S24_sol.ipynb").write_text(sol_json, encoding="utf-8")
    (sub / "001.ipynb").write_text(sol_json, encoding="utf-8")
    headers = [
        "anon_id",
        "Total Score",
        "Max Points",
        "9.9: 1.2 (3.0 pts)",
        "8.8: 1.1 (2.0 pts)",
    ]
    _write_csv(
        lab / "grades.csv",
        headers,
        [["001", "5", "5", "30", "40"]],
    )
    canon.canonicalize(
        lab,
        section_regex=r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)(?!\.)",
        question_regex=r"(?im)^\s*-?\s*(\d+)\s*<font[^>]*>.*?\[\s*(\d+)\s*pts?\s*\]",
        allow_misaligned_human=False,
    )
    with open(lab / "human_grades.csv", newline="", encoding="utf-8") as f:
        r = list(csv.DictReader(f))[0]
    assert r["1.1"] == "40"
    assert r["1.2"] == "30"


def test_canonicalize_allow_misaligned_writes_empty_question_scores(tmp_path):
    import subprocess

    root = Path(__file__).resolve().parent.parent
    canon = _load_script("canonicalize_experiment_lab.py")
    lab = tmp_path / "LabTest_Mis2"
    sub = lab / "submissions"
    sub.mkdir(parents=True)
    sol = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [
            _md_cell("# <font color='purple'>1</font>\n"),
            _md_cell("- 1 <font color='blue'> [2 pts] q1"),
            _md_cell("- 2 <font color='blue'> [3 pts] q2"),
        ],
    }
    sol_json = json.dumps(sol)
    (lab / "LabTest_Mis2_S24_sol.ipynb").write_text(sol_json, encoding="utf-8")
    (sub / "001.ipynb").write_text(sol_json, encoding="utf-8")
    headers = [
        "anon_id",
        "Total Score",
        "Max Points",
        "1: Autograder (0.0 pts)",
    ] + [f"2.{i}: Question ({i}.0 pts)" for i in range(1, 11)]
    row = ["001", "5", "50", "0.0"] + ["1.0"] * 10
    _write_csv(lab / "grades.csv", headers, [row])
    canon.canonicalize(
        lab,
        section_regex=r"(?m)^\s*#\s*<font[^>]*>\s*(\d+)\b",
        question_regex=r"(?im)^\s*-?\s*(\d+)\s*<font[^>]*>.*?\[\s*(\d+)\s*pts?\s*\]",
        allow_misaligned_human=True,
    )
    hg = (lab / "human_grades.csv").read_text(encoding="utf-8").splitlines()
    assert hg[0].startswith("anon_id,Total Score,Max Points,1.1,1.2")
    assert hg[1].endswith(",,")  # empty 1.1 and 1.2 scores
    r = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "verify_experiment_lab_layout.py"),
            "--lab-dir",
            str(lab),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr + r.stdout
