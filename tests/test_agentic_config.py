"""Tests for agentic config parsing and lazy CrewAI imports."""

import sys

import pytest
from pydantic import ValidationError

from config_models import AppConfig, ensure_app_config


class TestAgenticConfig:
    def test_default_disabled(self):
        cfg = AppConfig.model_validate({"assignment_name": "T"})
        assert cfg.agentic.enabled is False
        assert cfg.agentic.crew_type == "lean"

    def test_enabled_parses(self):
        cfg = ensure_app_config(
            {
                "assignment_name": "T",
                "agentic": {"enabled": True, "crew_type": "verified"},
            }
        )
        assert cfg.agentic.enabled is True
        assert cfg.agentic.crew_type == "verified"

    def test_invalid_crew_type_raises(self):
        with pytest.raises(ValidationError):
            AppConfig.model_validate(
                {
                    "assignment_name": "T",
                    "agentic": {"enabled": True, "crew_type": "turbo"},
                }
            )

    def test_crew_type_case_insensitive(self):
        cfg = ensure_app_config(
            {
                "assignment_name": "T",
                "agentic": {"crew_type": "PANEL"},
            }
        )
        assert cfg.agentic.crew_type == "panel"


class TestLazyImports:
    def test_import_agentic_does_not_import_crewai(self):
        mods = set(sys.modules)
        import agentic  # noqa: F401

        assert agentic.CrewType is not None
        assert "crewai" not in sys.modules or "crewai" in mods

    def test_config_parse_does_not_import_crewai(self):
        mods = set(sys.modules)
        ensure_app_config({"assignment_name": "T", "agentic": {"enabled": True}})
        assert "crewai" not in sys.modules or "crewai" in mods
