"""Pytest configuration. Ensures project root is on path."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
