"""Selectable crew profiles.

Each profile controls how many agents participate and how they are wired, for BOTH
the rubric crews and the grading crew:

- LEAN     : closest to the original single-call behavior.
             rubric  = Author -> Reviewer (sequential)
             grading = single Grader
- VERIFIED : adds an explicit checking stage.
             rubric  = Author -> Reviewer -> Validator (enforces fairness + deduction-sum)
             grading = Grader -> Verifier/adjudicator on borderline scores
- PANEL    : multiple specialized agents + a synthesizer/adjudicator.
             rubric  = Author + FairnessAuditor + Calibrator -> Synthesizer
             grading = N lens-graders (correctness / rubric-literal / leniency) -> Adjudicator
"""

from __future__ import annotations

from enum import Enum


class CrewType(str, Enum):
    LEAN = "lean"
    VERIFIED = "verified"
    PANEL = "panel"

    @classmethod
    def from_value(cls, value) -> "CrewType":
        """Coerce a config string into a CrewType, defaulting to LEAN."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return cls.LEAN
