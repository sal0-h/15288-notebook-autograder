"""Tests for llm/client helpers."""

from llm.client import temperature_for_model


def test_temperature_for_model_gpt41_and_mini():
    assert temperature_for_model("gpt-4.1") == 0.0
    assert temperature_for_model("gpt-4.1-mini") == 0.0


def test_temperature_for_model_early_gpt5():
    assert temperature_for_model("gpt-5") == 1.0
    assert temperature_for_model("gpt-5-mini") == 1.0
    assert temperature_for_model("gpt-5-mini-2025-08-07") == 1.0


def test_temperature_for_model_gpt5_dot_releases():
    assert temperature_for_model("gpt-5.2") == 0.0
    assert temperature_for_model("gpt-5.4-mini") == 0.0


def test_temperature_for_model_codex_defaults_high():
    assert temperature_for_model("gpt-5.2-codex") == 1.0
    assert temperature_for_model("gpt-5.3-codex") == 1.0
