"""Run HW1 grading multiple times across multiple models and measure nondeterminism.

Programmatic only (no argparse):
- Loads HW1 config from output/HW1/config.yaml
- Runs 5 times per model for: gpt-5-mini, gpt-5, gpt-4.1-mini, gpt-4.1
- Stores each run in an isolated output folder (no overwrite of base results)
- Computes score/feedback stability metrics across runs per model
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path

from batch_grader import grade_all_students
from grading_models import MODEL_PRICING
from utils import (
    DEFAULT_MODEL,
    ensure_app_config,
    load_config,
    setup_assignment_logging,
)

# ---------------------------------------------------------------------------
# Experiment configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# HW1 config source
SOURCE_CONFIG_PATH = PROJECT_ROOT / "output" / "HW1" / "config.yaml"

# Models to test (4 models × 5 runs each)
MODELS = ["gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4.1"]

# Base experiment settings (model is overridden per run)
EXPERIMENT = {
    "assignment_name": "HW1",
    "runs": 5,
    "sleep_seconds_between_runs": 1.0,
    "resume_existing_runs": True,
    "salvage_partial_runs": True,
    "rerun_incomplete_runs": True,
    "strict_resume_validation": False,
}

OUTPUT_SUBDIR = "nondeterminism"
SCORE_DECIMALS = 6
RUN_COMPLETE_MARKER = ".run_complete"


@dataclass
class RunArtifact:
    run_index: int
    run_output_dir: Path
    started_at_epoch_s: float
    finished_at_epoch_s: float
    graded_results: list[dict]
    usage: dict[str, int]


def _deep_update(base: dict, overrides: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_update(out[key], value)
        else:
            out[key] = value
    return out


def _load_base_config(model: str) -> dict:
    cfg = ensure_app_config(load_config(SOURCE_CONFIG_PATH)).model_dump()
    cfg = _deep_update(cfg, {"model": model})
    return ensure_app_config(cfg).model_dump()


def _sort_key_qid(qid: str) -> tuple[int, int] | tuple[int, str]:
    parts = str(qid).split(".")
    if len(parts) == 2:
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    return (999_999, str(qid))


def _round_score(v: float) -> float:
    return round(float(v), SCORE_DECIMALS)


def _mean(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _safe_std(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    return float(statistics.pstdev(values))


def _format_duration(seconds: float) -> str:
    total = int(round(max(seconds, 0.0)))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(text).strip()).strip("-")
    return value.lower() or "experiment"


def _build_experiment_name(base_cfg: dict, model: str) -> str:
    ts = datetime.now().strftime("%Y%m%d")
    assignment = _slug(base_cfg.get("assignment_name", "assignment"))
    workers = int(base_cfg.get("workers", 1))
    runs = int(EXPERIMENT.get("runs", 5))
    label = _slug(model)
    return f"{label}__{assignment}__w{workers}__r{runs}__{ts}"


def _prepare_experiment_root(base_output_dir: Path, experiment_name: str) -> Path:
    exp_root = base_output_dir / OUTPUT_SUBDIR / experiment_name
    exp_root.mkdir(parents=True, exist_ok=True)
    return exp_root


def _build_run_config(base_config: dict, run_output_dir: Path) -> dict:
    cfg = copy.deepcopy(base_config)

    source_output_dir = Path(base_config["output_dir"])
    source_solution_parsed = source_output_dir / "solution_parsed.json"
    source_parsed_dir = Path(base_config["parsed_dir"])
    source_submissions_dir = Path(base_config.get("submissions_dir", ""))

    if not source_solution_parsed.exists():
        raise FileNotFoundError(
            f"Missing source solution parse: {source_solution_parsed}. Run parse first."
        )
    if not source_parsed_dir.exists():
        raise FileNotFoundError(
            f"Missing source parsed dir: {source_parsed_dir}. Run parse first."
        )

    run_output_dir.mkdir(parents=True, exist_ok=True)
    target_solution_parsed = run_output_dir / "solution_parsed.json"
    if not target_solution_parsed.exists():
        shutil.copy2(source_solution_parsed, target_solution_parsed)

    cfg["output_dir"] = str(run_output_dir)
    cfg["parsed_dir"] = str(source_parsed_dir)
    cfg["submissions_dir"] = str(source_submissions_dir) if source_submissions_dir else ""

    return ensure_app_config(cfg).model_dump()


def _run_once(run_index: int, run_config: dict, run_output_dir: Path) -> RunArtifact:
    started = time.time()
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    assignment_name = str(run_config.get("assignment_name", "HW1"))
    setup_assignment_logging(assignment_name, run_output_dir)

    for evt in grade_all_students(run_config):
        if evt.get("status") == "usage":
            usage = evt.get("usage", {})
            usage_total["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
            usage_total["completion_tokens"] += int(
                usage.get("completion_tokens", 0) or 0
            )

    graded_path = run_output_dir / "graded_results.json"
    if not graded_path.exists():
        raise FileNotFoundError(f"Expected graded results not found: {graded_path}")

    graded_results = json.loads(graded_path.read_text(encoding="utf-8"))
    graded_results.sort(key=lambda r: str(r.get("student_name", "")))

    finished = time.time()
    return RunArtifact(
        run_index=run_index,
        run_output_dir=run_output_dir,
        started_at_epoch_s=started,
        finished_at_epoch_s=finished,
        graded_results=graded_results,
        usage=usage_total,
    )


def _load_existing_run_artifact(
    run_index: int, run_output_dir: Path
) -> RunArtifact | None:
    graded_path = run_output_dir / "graded_results.json"
    if not graded_path.exists():
        return None

    try:
        graded_results = json.loads(graded_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(graded_results, list):
        return None

    graded_results.sort(key=lambda r: str(r.get("student_name", "")))

    started = 0.0
    finished = 0.0
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}

    run_summary_path = run_output_dir / "run_summary.json"
    marker_path = run_output_dir / RUN_COMPLETE_MARKER
    has_completion_marker = marker_path.exists()
    strict_resume = bool(EXPERIMENT.get("strict_resume_validation", False))

    if strict_resume and not has_completion_marker and not run_summary_path.exists():
        return None

    if run_summary_path.exists():
        try:
            run_summary = json.loads(run_summary_path.read_text(encoding="utf-8"))
            if strict_resume and not bool(run_summary.get("completed", False)):
                return None
            duration_s = float(run_summary.get("duration_seconds", 0.0) or 0.0)
            started = 0.0
            finished = max(duration_s, 0.0)
            usage_total["prompt_tokens"] = int(
                run_summary.get("prompt_tokens", 0) or 0
            )
            usage_total["completion_tokens"] = int(
                run_summary.get("completion_tokens", 0) or 0
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    if strict_resume and not has_completion_marker:
        return None

    return RunArtifact(
        run_index=run_index,
        run_output_dir=run_output_dir,
        started_at_epoch_s=started,
        finished_at_epoch_s=finished,
        graded_results=graded_results,
        usage=usage_total,
    )


def _classify_run_state(artifact: RunArtifact | None, expected_students: int) -> str:
    if artifact is None:
        return "missing"
    count = len(artifact.graded_results)
    if count == expected_students:
        return "complete"
    if 0 <= count < expected_students:
        return "partial"
    return "invalid"


def _expected_students_count(base_cfg: dict) -> int:
    parsed_dir = Path(base_cfg["parsed_dir"])
    return len(sorted(parsed_dir.glob("*.json")))


def _run_summary_from_artifact(artifact: RunArtifact) -> dict:
    duration_s = artifact.finished_at_epoch_s - artifact.started_at_epoch_s
    return {
        "run_index": artifact.run_index,
        "run_output_dir": str(artifact.run_output_dir),
        "duration_seconds": round(duration_s, 3),
        "duration_human": _format_duration(duration_s),
        "prompt_tokens": int(artifact.usage.get("prompt_tokens", 0)),
        "completion_tokens": int(artifact.usage.get("completion_tokens", 0)),
        "students": len(artifact.graded_results),
        "graded_results_path": str(artifact.run_output_dir / "graded_results.json"),
        "completed": True,
    }


def _persist_run_summary(artifact: RunArtifact, expected_students: int) -> None:
    run_summary = _run_summary_from_artifact(artifact)
    students_count = len(artifact.graded_results)
    run_summary["students"] = students_count
    run_summary["expected_students"] = expected_students
    run_summary["completed"] = students_count == expected_students
    (artifact.run_output_dir / "run_summary.json").write_text(
        json.dumps(run_summary, indent=2), encoding="utf-8"
    )
    marker_path = artifact.run_output_dir / RUN_COMPLETE_MARKER
    if run_summary["completed"]:
        marker_path.write_text("ok\n", encoding="utf-8")
    elif marker_path.exists():
        marker_path.unlink()


def _index_results_by_student(run: RunArtifact) -> dict[str, dict]:
    return {str(r.get("student_name", "Unknown")): r for r in run.graded_results}


def _build_metrics(runs: list[RunArtifact]) -> dict:
    if not runs:
        raise ValueError("No runs to compare")

    run_maps = [_index_results_by_student(run) for run in runs]
    student_names = sorted(set().union(*[set(m.keys()) for m in run_maps]))

    total_exact_count = 0
    total_std_values: list[float] = []
    total_range_values: list[float] = []
    student_instability: list[dict] = []

    qpair_exact_count = 0
    qpair_count = 0
    qpair_std_values: list[float] = []
    qpair_range_values: list[float] = []
    qpair_feedback_flip = 0
    qpair_review_flip = 0
    qpair_confidence_flip = 0

    per_question_std: dict[str, list[float]] = {}
    per_question_range: dict[str, list[float]] = {}

    for student in student_names:
        totals: list[float] = []
        question_ids: set[str] = set()

        for run_map in run_maps:
            entry = run_map.get(student)
            if not entry:
                continue
            totals.append(_round_score(float(entry.get("total_score", 0.0))))
            question_ids.update((entry.get("questions") or {}).keys())

        if not totals:
            continue

        if len(set(totals)) == 1:
            total_exact_count += 1

        total_std = _safe_std(totals)
        total_range = max(totals) - min(totals)
        total_std_values.append(total_std)
        total_range_values.append(total_range)
        student_instability.append(
            {
                "student_name": student,
                "total_std": round(total_std, 6),
                "total_range": round(total_range, 6),
                "totals": totals,
            }
        )

        for qid in sorted(question_ids, key=_sort_key_qid):
            scores: list[float] = []
            feedbacks: list[str] = []
            reviews: list[bool] = []
            confidences: list[str] = []

            for run_map in run_maps:
                entry = run_map.get(student, {})
                q = (entry.get("questions") or {}).get(qid)
                if q is None:
                    continue
                scores.append(_round_score(float(q.get("score", 0.0))))
                feedbacks.append(str(q.get("feedback", "")).strip())
                reviews.append(bool(q.get("requires_review", False)))
                confidences.append(str(q.get("confidence", "")))

            if not scores:
                continue

            qpair_count += 1
            if len(set(scores)) == 1:
                qpair_exact_count += 1

            q_std = _safe_std(scores)
            q_range = max(scores) - min(scores)
            qpair_std_values.append(q_std)
            qpair_range_values.append(q_range)

            if len(set(feedbacks)) > 1:
                qpair_feedback_flip += 1
            if len(set(reviews)) > 1:
                qpair_review_flip += 1
            if len(set(confidences)) > 1:
                qpair_confidence_flip += 1

            per_question_std.setdefault(qid, []).append(q_std)
            per_question_range.setdefault(qid, []).append(q_range)

    pairwise_rows: list[dict] = []
    for i, j in combinations(range(len(runs)), 2):
        a = run_maps[i]
        b = run_maps[j]
        common_students = sorted(set(a.keys()) & set(b.keys()))

        total_abs_diffs: list[float] = []
        total_exact = 0
        question_abs_diffs: list[float] = []

        for student in common_students:
            at = _round_score(float(a[student].get("total_score", 0.0)))
            bt = _round_score(float(b[student].get("total_score", 0.0)))
            total_abs_diffs.append(abs(at - bt))
            if at == bt:
                total_exact += 1

            aq = a[student].get("questions", {})
            bq = b[student].get("questions", {})
            for qid in set(aq.keys()) | set(bq.keys()):
                ascore = _round_score(float((aq.get(qid) or {}).get("score", 0.0)))
                bscore = _round_score(float((bq.get(qid) or {}).get("score", 0.0)))
                question_abs_diffs.append(abs(ascore - bscore))

        pairwise_rows.append(
            {
                "run_a": i + 1,
                "run_b": j + 1,
                "students_compared": len(common_students),
                "mean_abs_total_diff": round(_mean(total_abs_diffs), 6),
                "exact_total_match_rate": round(
                    (total_exact / len(common_students)) if common_students else 0.0,
                    6,
                ),
                "mean_abs_question_diff": round(_mean(question_abs_diffs), 6),
            }
        )

    unstable_students = sorted(
        student_instability,
        key=lambda x: (x["total_std"], x["total_range"]),
        reverse=True,
    )

    question_instability: list[dict] = []
    for qid in sorted(per_question_std.keys(), key=_sort_key_qid):
        std_vals = per_question_std[qid]
        range_vals = per_question_range.get(qid, [])
        question_instability.append(
            {
                "qid": qid,
                "mean_std": round(_mean(std_vals), 6),
                "max_std": round(max(std_vals) if std_vals else 0.0, 6),
                "mean_range": round(_mean(range_vals), 6),
                "max_range": round(max(range_vals) if range_vals else 0.0, 6),
                "pairs_observed": len(std_vals),
            }
        )

    question_instability.sort(
        key=lambda x: (x["mean_std"], x["max_range"]), reverse=True
    )

    return {
        "summary": {
            "num_runs": len(runs),
            "students_observed": len(student_names),
            "student_total_exact_rate": round(
                total_exact_count / len(student_names) if student_names else 0.0,
                6,
            ),
            "student_total_mean_std": round(_mean(total_std_values), 6),
            "student_total_max_range": round(
                max(total_range_values) if total_range_values else 0.0, 6
            ),
            "qpair_count": qpair_count,
            "qpair_exact_score_rate": round(
                qpair_exact_count / qpair_count if qpair_count else 0.0,
                6,
            ),
            "qpair_mean_std": round(_mean(qpair_std_values), 6),
            "qpair_max_range": round(
                max(qpair_range_values) if qpair_range_values else 0.0, 6
            ),
            "qpair_feedback_flip_rate": round(
                qpair_feedback_flip / qpair_count if qpair_count else 0.0,
                6,
            ),
            "qpair_review_flip_rate": round(
                qpair_review_flip / qpair_count if qpair_count else 0.0,
                6,
            ),
            "qpair_confidence_flip_rate": round(
                qpair_confidence_flip / qpair_count if qpair_count else 0.0,
                6,
            ),
        },
        "pairwise_runs": pairwise_rows,
        "top_unstable_students": unstable_students[:20],
        "top_unstable_questions": question_instability[:20],
    }


def _usage_and_cost_summary(runs: list[RunArtifact], model: str) -> dict:
    prompt = sum(run.usage.get("prompt_tokens", 0) for run in runs)
    completion = sum(run.usage.get("completion_tokens", 0) for run in runs)
    inp_cost, out_cost = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
    usd = (prompt / 1_000_000.0) * inp_cost + (completion / 1_000_000.0) * out_cost

    return {
        "model": model,
        "prompt_tokens": int(prompt),
        "completion_tokens": int(completion),
        "estimated_cost_usd": round(usd, 4),
    }


def _write_outputs(
    exp_root: Path,
    runs: list[RunArtifact],
    metrics: dict,
    model: str,
    assignment_name: str,
    expected_students: int,
) -> None:
    total_duration_s = sum(
        artifact.finished_at_epoch_s - artifact.started_at_epoch_s
        for artifact in runs
    )

    run_index_summary: list[dict] = []
    for artifact in runs:
        run_summary = _run_summary_from_artifact(artifact)
        run_index_summary.append(run_summary)
        _persist_run_summary(artifact, expected_students=expected_students)

    envelope = {
        "source_config_path": str(SOURCE_CONFIG_PATH),
        "model": model,
        "n_runs": len(runs),
        "created_at_epoch_s": time.time(),
        "total_duration_seconds": round(total_duration_s, 3),
        "total_duration_human": _format_duration(total_duration_s),
        "usage": _usage_and_cost_summary(runs, model),
        "runs": run_index_summary,
        "metrics": metrics,
    }

    metrics_path = exp_root / "nondeterminism_metrics.json"
    metrics_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")

    summary = metrics["summary"]
    base_results_path = SOURCE_CONFIG_PATH.parent / "graded_results.json"
    md_lines = [
        f"# Nondeterminism Report — HW1 ({model})",
        "",
        f"- Runs: **{len(runs)}**",
        f"- Model: **{model}**",
        f"- Students: **{summary['students_observed']}**",
        f"- Total runtime: **{envelope['total_duration_human']}**",
        f"- Estimated cost: **${envelope['usage']['estimated_cost_usd']:.4f}**",
        "",
        "## Output Isolation",
        "",
        "- Each run writes to its own folder under this experiment root.",
        f"- Base assignment `{base_results_path}` is not modified.",
        "",
        "## Stability Summary",
        "",
        f"- Student total exact match rate: **{summary['student_total_exact_rate']:.2%}**",
        f"- Student total mean std: **{summary['student_total_mean_std']:.4f}**",
        f"- Student total max range: **{summary['student_total_max_range']:.4f}**",
        f"- Question-pair exact score rate: **{summary['qpair_exact_score_rate']:.2%}**",
        f"- Question-pair mean std: **{summary['qpair_mean_std']:.4f}**",
        f"- Question-pair max range: **{summary['qpair_max_range']:.4f}**",
        f"- Feedback flip rate: **{summary['qpair_feedback_flip_rate']:.2%}**",
        f"- requires_review flip rate: **{summary['qpair_review_flip_rate']:.2%}**",
        f"- Confidence flip rate: **{summary['qpair_confidence_flip_rate']:.2%}**",
        "",
        "## Top Unstable Students",
        "",
    ]

    for row in metrics["top_unstable_students"][:10]:
        md_lines.append(
            f"- {row['student_name']}: std={row['total_std']:.4f}, range={row['total_range']:.4f}, totals={row['totals']}"
        )

    md_lines.extend(["", "## Top Unstable Questions", ""])
    for row in metrics["top_unstable_questions"][:10]:
        md_lines.append(
            f"- Q{row['qid']}: mean_std={row['mean_std']:.4f}, max_range={row['max_range']:.4f}, observed_pairs={row['pairs_observed']}"
        )

    (exp_root / "REPORT.md").write_text("\n".join(md_lines), encoding="utf-8")

    print(f"  Experiment artifacts: {exp_root}")
    print(f"  Metrics: {metrics_path}")
    print(f"  Report: {exp_root / 'REPORT.md'}")


def run_nondeterminism_for_model(model: str) -> None:
    """Run 5 grading runs for a single model."""
    base_cfg = _load_base_config(model)
    base_output_dir = Path(base_cfg["output_dir"])
    workers = int(base_cfg.get("workers", 1))
    n_runs = int(EXPERIMENT.get("runs", 5))
    sleep_s = float(EXPERIMENT.get("sleep_seconds_between_runs", 0.0))
    resume_existing_runs = bool(EXPERIMENT.get("resume_existing_runs", True))
    salvage_partial_runs = bool(EXPERIMENT.get("salvage_partial_runs", True))
    rerun_incomplete_runs = bool(EXPERIMENT.get("rerun_incomplete_runs", True))
    strict_resume_validation = bool(EXPERIMENT.get("strict_resume_validation", False))
    expected_students = _expected_students_count(base_cfg)
    experiment_name = _build_experiment_name(base_cfg, model)

    exp_root = _prepare_experiment_root(base_output_dir, experiment_name)

    print(
        f"\n{'='*60}\nModel: {model} | Workers: {workers} | Runs: {n_runs}\n{'='*60}"
    )
    print(f"Source config: {SOURCE_CONFIG_PATH}")
    print(f"Experiment: {experiment_name}")
    print(f"Expected students per run: {expected_students}")

    runs: list[RunArtifact] = []
    experiment_started = time.time()
    for i in range(1, n_runs + 1):
        run_dir = exp_root / f"run_{i:02d}"

        existing = (
            _load_existing_run_artifact(i, run_dir) if resume_existing_runs else None
        )
        state = _classify_run_state(existing, expected_students)

        if state == "complete" and existing is not None:
            print(
                f"  Reusing existing run {i}/{n_runs} -> {run_dir.name} "
                f"(students={len(existing.graded_results)})"
            )
            _persist_run_summary(existing, expected_students)
            runs.append(existing)
            continue

        if state == "partial" and existing is not None:
            existing_count = len(existing.graded_results)
            if salvage_partial_runs:
                print(
                    f"  Resuming partial run {i}/{n_runs} ({existing_count}/{expected_students})"
                )
            elif rerun_incomplete_runs:
                print(
                    f"  Discarding partial run {i}/{n_runs} ({existing_count}/{expected_students}) and rerunning"
                )
                if run_dir.exists():
                    shutil.rmtree(run_dir)
            else:
                print(
                    f"  Partial run {i}/{n_runs} ({existing_count}/{expected_students}) will be continued"
                )

        if (
            state in {"missing", "invalid"}
            and run_dir.exists()
            and rerun_incomplete_runs
        ):
            shutil.rmtree(run_dir)

        run_cfg = _build_run_config(base_cfg, run_dir)

        print(f"  Starting run {i}/{n_runs} -> {run_dir.name}")
        artifact = _run_once(i, run_cfg, run_dir)
        duration = artifact.finished_at_epoch_s - artifact.started_at_epoch_s
        completed = len(runs) + 1
        elapsed = artifact.finished_at_epoch_s - experiment_started
        mean_per_run = elapsed / completed
        remaining = max(n_runs - completed, 0) * mean_per_run
        print(
            f"  Finished run {i}/{n_runs} in {_format_duration(duration)} "
            f"(prompt={artifact.usage['prompt_tokens']}, completion={artifact.usage['completion_tokens']})"
        )
        if len(artifact.graded_results) != expected_students:
            print(
                f"  Warning: run {i} saved {len(artifact.graded_results)}/{expected_students} students."
            )
        if completed < n_runs:
            print(
                f"  Progress: {completed}/{n_runs} | "
                f"elapsed={_format_duration(elapsed)} | eta~{_format_duration(remaining)}"
            )
        _persist_run_summary(artifact, expected_students)
        runs.append(artifact)

        if i < n_runs and sleep_s > 0:
            time.sleep(sleep_s)

    metrics = _build_metrics(runs)
    _write_outputs(
        exp_root,
        runs,
        metrics,
        model,
        str(base_cfg.get("assignment_name", "HW1")),
        expected_students,
    )


def main() -> None:
    print("HW1 Nondeterminism Experiment")
    print(f"Source config: {SOURCE_CONFIG_PATH}")
    print(f"Models: {MODELS}")
    print(f"Runs per model: {EXPERIMENT['runs']}")

    if not SOURCE_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"HW1 config not found: {SOURCE_CONFIG_PATH}. Run setup and parse first."
        )

    for model in MODELS:
        run_nondeterminism_for_model(model)

    print("\n" + "=" * 60)
    print("All models completed. Check output/HW1/nondeterminism/<model>__hw1__*/ for reports.")


if __name__ == "__main__":
    main()
