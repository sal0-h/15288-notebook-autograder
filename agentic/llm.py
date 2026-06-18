"""CrewAI LLM factory.

Reuses the project's existing OpenAI key lookup and the GPT-5 temperature policy
from llm.client, so agentic runs use the SAME models and credentials as the
simple-LLM path. CrewAI routes through litellm, which expects provider-prefixed
model ids for OpenAI (``openai/<model>``).
"""

from __future__ import annotations

import os
import warnings

from dotenv import load_dotenv

from config_models import DEFAULT_MODEL
from llm.client import temperature_for_model


def _api_key() -> str:
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key and os.environ.get("key"):
        warnings.warn(
            "Using 'key' in .env is deprecated; set OPENAI_API_KEY instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        api_key = os.environ.get("key")
    if not api_key:
        raise ValueError("API key not found. Set OPENAI_API_KEY in .env.")
    return api_key


def _litellm_model_id(model: str) -> str:
    """litellm wants an explicit provider prefix for OpenAI models."""
    if "/" in model:
        return model
    return f"openai/{model}"


def build_crew_llm(model: str | None = None, *, temperature: float | None = None):
    """Return a crewai.LLM for the given model. Imported lazily so crewai stays optional."""
    from crewai import LLM  # noqa: PLC0415 — keep crewai an optional dependency

    model = model or DEFAULT_MODEL
    if temperature is None:
        temperature = temperature_for_model(model)
    return LLM(
        model=_litellm_model_id(model),
        temperature=temperature,
        api_key=_api_key(),
    )
