"""OpenAI client creation and model-specific helpers."""

from __future__ import annotations

import os
import warnings

import httpx
from dotenv import load_dotenv
from openai import OpenAI


def temperature_for_model(model: str) -> float:
    """Return sampling temperature for Chat Completions (0 = deterministic when allowed).

    Early GPT-5 ids (``gpt-5``, ``gpt-5-mini``, ``gpt-5-nano``, dated snapshots) often
    only accept the default temperature (``1``). Newer dot-releases (``gpt-5.2``+,
    ``gpt-5.3``+, …) typically accept ``0``; Codex variants are treated like the older
    policy (``1``) until confirmed otherwise. Verify with ``test_openai_connection.py
    --probe-chat --probe-json`` for your org.
    """
    m = model.lower()
    if not m.startswith("gpt-5"):
        return 0.0
    if "codex" in m:
        return 1.0
    if m.startswith(("gpt-5.5", "gpt-5.4", "gpt-5.3", "gpt-5.2")):
        return 0.0
    return 1.0


def get_openai_client(
    max_retries: int = 5,
    *,
    max_connections: int = 20,
    max_keepalive_connections: int = 20,
    connect_timeout_s: float = 10.0,
    read_timeout_s: float = 120.0,
    write_timeout_s: float = 30.0,
    pool_timeout_s: float = 30.0,
) -> OpenAI:
    """Initialize OpenAI client with .env key, with SDK-level retries.

    Prefer ``OPENAI_API_KEY``. The legacy ``key`` env var is deprecated and will be
    removed in a future release.
    """
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
    limits = httpx.Limits(
        max_connections=max(1, int(max_connections)),
        max_keepalive_connections=max(1, int(max_keepalive_connections)),
    )
    timeout = httpx.Timeout(
        connect=connect_timeout_s,
        read=read_timeout_s,
        write=write_timeout_s,
        pool=pool_timeout_s,
    )
    http_client = httpx.Client(limits=limits, timeout=timeout)
    return OpenAI(
        api_key=api_key,
        max_retries=max_retries,
        http_client=http_client,
    )
