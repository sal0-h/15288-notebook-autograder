"""CLI: ``python -m rubric`` / ``rubric.impl`` entry point."""

import argparse
import json
import logging
from pathlib import Path

from rubric.generate import generate_rubrics
from utils import load_app_config


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(
        description="Generate rubrics from solution notebook"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to assignment config.yaml (required, typically output/{assignment_name}/config.yaml)",
    )
    args = parser.parse_args()
    if args.config is None:
        parser.error(
            "--config is required and must point to output/{assignment_name}/config.yaml"
        )

    config = load_app_config(args.config)
    rubrics = generate_rubrics(config)
    print(json.dumps(rubrics, indent=2))


if __name__ == "__main__":
    main()
