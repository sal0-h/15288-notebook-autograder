"""Post-grading calibration: z-score outlier detection per question."""

import json
import math
from pathlib import Path

from utils import load_config


def run_calibration(config: dict) -> list[dict[str, str | float]]:
    """
    Read graded_results.json and flag student-question pairs where the score
    is more than 2 standard deviations from the mean for that question.

    Returns a list of flagged entries:
    {student_name, qid, score, mean, std, z_score, flag_reason}
    """
    output_dir = Path(config.get("output_dir", "output"))
    graded_path = output_dir / "graded_results.json"

    if not graded_path.exists():
        raise FileNotFoundError(
            f"Graded results not found: {graded_path}. Run grading first."
        )

    results = json.loads(graded_path.read_text(encoding="utf-8"))
    if not results:
        return []

    # Collect scores per question: qid -> [(student_name, score, max_pts)]
    qid_scores: dict[str, list[tuple[str, float, float]]] = {}
    for r in results:
        student_name = r.get("student_name", "Unknown")
        for qid, q_data in (r.get("questions") or {}).items():
            score = float(q_data.get("score", 0))
            max_pts = float(q_data.get("max", 0))
            qid_scores.setdefault(qid, []).append((student_name, score, max_pts))

    # Compute mean and std per question, flag outliers
    flagged: list[dict] = []
    for qid, pairs in qid_scores.items():
        scores = [s for _, s, _ in pairs]
        n = len(scores)
        if n < 2:
            continue
        mean = sum(scores) / n
        # Use sample variance (n-1) for more stable outlier detection on small cohorts.
        variance = sum((x - mean) ** 2 for x in scores) / (n - 1)
        std = math.sqrt(variance) if variance > 0 else 0
        if std == 0:
            continue

        for student_name, score, max_pts in pairs:
            z = (score - mean) / std
            if abs(z) > 2:
                flag_reason = "low" if z < 0 else "high"
                flagged.append(
                    {
                        "student_name": student_name,
                        "qid": qid,
                        "score": score,
                        "max": max_pts,
                        "mean": round(mean, 2),
                        "std": round(std, 2),
                        "z_score": round(z, 2),
                        "flag_reason": flag_reason,
                    }
                )

    # Save report
    report_path = output_dir / "calibration_report.json"
    report_path.write_text(json.dumps(flagged, indent=2), encoding="utf-8")

    return flagged


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Run calibration (outlier detection) on graded results"
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()

    config = load_config(args.config)
    flagged = run_calibration(config)
    print(f"Calibration: {len(flagged)} outlier(s) flagged")
    for f in flagged[:20]:
        print(
            f"  {f['student_name']} Q{f['qid']}: score={f['score']}, mean={f['mean']}±{f['std']}, z={f['z_score']}"
        )
    if len(flagged) > 20:
        print(f"  ... and {len(flagged) - 20} more")


if __name__ == "__main__":
    main()
