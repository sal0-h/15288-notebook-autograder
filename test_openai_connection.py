"""Test script to verify OpenAI API connection and authentication.

Also supports:
  --list-models       GET /v1/models (ids your key can list; not the same as chat access).
  --probe-chat        Minimal POST /v1/chat/completions per candidate to see what actually works.

Chat probe seeds are aligned with OpenAI developer docs → Models → “All models”
(https://developers.openai.com/api/docs/models/all); update ``_CHAT_PROBE_SEED_EXTRA`` when the catalog changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass

from openai import APIStatusError, OpenAI

from config_models import DEFAULT_MODEL, load_app_config
from llm.client import get_openai_client, temperature_for_model
from token_usage import MODEL_PRICING


def test_llm_connection() -> str:
    """Send a basic prompt and return the confirmation response."""
    client = get_openai_client()
    cfg = load_app_config("config.yaml")
    model = cfg.model or DEFAULT_MODEL

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant. Reply briefly.",
            },
            {
                "role": "user",
                "content": "Please respond with a short confirmation that the API connection works.",
            },
        ],
    )

    return response.choices[0].message.content or ""


def list_available_models() -> list[str]:
    """List models returned by GET /v1/models for the current API key/project."""
    client = get_openai_client()
    models = client.models.list()
    return sorted(m.id for m in models.data)


# Substrings: ids from /v1/models unlikely to succeed on a plain text Chat Completions probe.
# Aligned with OpenAI "All models" groupings (image, video, realtime, embeddings, etc.):
# https://developers.openai.com/api/docs/models/all
_CHAT_PROBE_SKIP_SUBSTR = (
    "embed",
    "embedding",
    "moderation",
    "tts",
    "whisper",
    "transcribe",
    "dall-e",
    "davinci",
    "babbage",
    "ada-",
    "text-similarity",
    "text-search-",
    "code-search-",
    "instruct-beta",
    "similarity",
    "gpt-image",
    "chatgpt-image",
    "sora-",
    "gpt-realtime",
    "gpt-4o-realtime",
    "gpt-4o-mini-realtime",
    "computer-use",
    "search-preview",
    "deep-research",
)

# Curated ids from OpenAI developer docs → Models → "All models" (text / reasoning / codex /
# ChatGPT-branded chat, open-weight). Probe still decides what your key can call.
# Update this tuple when the catalog changes.
_CHAT_PROBE_SEED_EXTRA: tuple[str, ...] = tuple(
    sorted(
        {
            # Frontier (GPT-5.x family)
            "gpt-5",
            "gpt-5-mini",
            "gpt-5-nano",
            "gpt-5.1",
            "gpt-5.2",
            "gpt-5.2-pro",
            "gpt-5-pro",
            "gpt-5.3-chat-latest",
            "gpt-5.4",
            "gpt-5.4-mini",
            "gpt-5.4-nano",
            "gpt-5.4-pro",
            "gpt-5.5",
            "gpt-5.5-pro",
            "gpt-5-chat-latest",
            "gpt-5.1-chat-latest",
            "gpt-5.2-chat-latest",
            # GPT-4.x / GPT-4o
            "gpt-3.5-turbo",
            "gpt-3.5-turbo-0125",
            "gpt-3.5-turbo-16k",
            "gpt-4",
            "gpt-4-turbo",
            "gpt-4-turbo-preview",
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            "gpt-4.5-preview",
            "gpt-4o",
            "gpt-4o-2024-08-06",
            "gpt-4o-mini",
            "gpt-4o-mini-2024-07-18",
            # o-series reasoning
            "o1",
            "o1-mini",
            "o1-preview",
            "o1-pro",
            "o3",
            "o3-mini",
            "o3-pro",
            "o4-mini",
        }
    )
)


def _is_probable_text_chat_model_id(model_id: str) -> bool:
    mid = model_id.lower()
    if any(s in mid for s in _CHAT_PROBE_SKIP_SUBSTR):
        return False
    # Fine-tunes / org models: still chat-shaped, but skip very long opaque ids unless from list
    if mid.startswith("ft:"):
        return False
    return bool(
        re.match(
            r"^(gpt-[34]|gpt-5|gpt-oss|chatgpt-|o\d|codex-)",
            mid,
        )
    )


def probe_candidate_model_ids(*, use_list: bool, use_seed: bool) -> list[str]:
    """Union of filtered /v1/models ids and curated seeds (sorted, deduped)."""
    found: set[str] = set()
    if use_seed:
        found.update(MODEL_PRICING.keys())
        found.update(_CHAT_PROBE_SEED_EXTRA)
    if use_list:
        client = get_openai_client()
        listed = client.models.list()
        for m in listed.data:
            if _is_probable_text_chat_model_id(m.id):
                found.add(m.id)
    return sorted(found)


def _api_error_text(exc: BaseException) -> str:
    if isinstance(exc, APIStatusError):
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
        return str(exc).strip() or repr(exc)
    return str(exc).strip() or repr(exc)


def _minimal_chat_create(
    client: OpenAI,
    *,
    model: str,
    temperature: float | None,
    use_max_completion_tokens: bool,
    per_request_timeout: float | None,
) -> tuple[object, str]:
    """Returns (response, attempt_label)."""
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    if use_max_completion_tokens:
        kwargs["max_completion_tokens"] = 8
    else:
        kwargs["max_tokens"] = 8
    if per_request_timeout is not None:
        kwargs["timeout"] = per_request_timeout
    label = (
        f"temp={temperature!s}, "
        f"{'max_completion_tokens' if use_max_completion_tokens else 'max_tokens'}=8"
    )
    try:
        return client.chat.completions.create(**kwargs), label
    except TypeError:
        kwargs.pop("timeout", None)
        return (
            client.chat.completions.create(**kwargs),
            label + " (no per-call timeout)",
        )


@dataclass(frozen=True)
class ChatProbeResult:
    model: str
    ok: bool
    temperature_used: str  # "0", "1", "default", "-"
    max_token_style: str  # "max_tokens", "max_completion_tokens", "-"
    note: str


def probe_chat_completions_for_model(
    client: OpenAI,
    model: str,
    *,
    per_request_timeout: float | None,
) -> ChatProbeResult:
    """
    Try a tiny chat completion; try parameter combinations in a sensible order.
    """
    # Prefer max_tokens + temp 0 (broad compatibility), then relax temperature,
    # then switch to max_completion_tokens (newer models).
    attempts: list[tuple[bool, float | None]] = []
    for use_mct in (False, True):
        for temp in (0.0, 1.0, None):
            attempts.append((use_mct, temp))

    last_err = ""
    for use_mct, temp in attempts:
        try:
            _resp, label = _minimal_chat_create(
                client,
                model=model,
                temperature=temp,
                use_max_completion_tokens=use_mct,
                per_request_timeout=per_request_timeout,
            )
            temp_used = "default" if temp is None else ("0" if temp == 0.0 else "1")
            style = "max_completion_tokens" if use_mct else "max_tokens"
            return ChatProbeResult(
                model=model,
                ok=True,
                temperature_used=temp_used,
                max_token_style=style,
                note=label,
            )
        except APIStatusError as e:
            last_err = _api_error_text(e)
            if e.status_code == 429:
                time.sleep(1.5)
                try:
                    _resp, label = _minimal_chat_create(
                        client,
                        model=model,
                        temperature=temp,
                        use_max_completion_tokens=use_mct,
                        per_request_timeout=per_request_timeout,
                    )
                    temp_used = (
                        "default" if temp is None else ("0" if temp == 0.0 else "1")
                    )
                    style = "max_completion_tokens" if use_mct else "max_tokens"
                    return ChatProbeResult(
                        model=model,
                        ok=True,
                        temperature_used=temp_used,
                        max_token_style=style,
                        note=label + " (after 429 retry)",
                    )
                except Exception as e2:  # noqa: BLE001
                    last_err = _api_error_text(e2)
            continue
        except Exception as e:  # noqa: BLE001
            last_err = _api_error_text(e)
            continue

    return ChatProbeResult(
        model=model,
        ok=False,
        temperature_used="-",
        max_token_style="-",
        note=last_err[:240] + ("…" if len(last_err) > 240 else ""),
    )


def run_chat_probe(
    *,
    use_list: bool,
    use_seed: bool,
    per_request_timeout: float | None,
    json_out: bool,
) -> int:
    client = get_openai_client()
    models = probe_candidate_model_ids(use_list=use_list, use_seed=use_seed)
    if not models:
        print(
            "No candidate model ids. Do not pass both --probe-no-list and --probe-no-seed.",
            file=sys.stderr,
        )
        return 2

    if not json_out:
        print(
            f"Probing {len(models)} candidate model ids via chat.completions…",
            file=sys.stderr,
        )

    results: list[ChatProbeResult] = []
    for mid in models:
        results.append(
            probe_chat_completions_for_model(
                client, mid, per_request_timeout=per_request_timeout
            )
        )

    if json_out:
        payload = [
            {
                "model": r.model,
                "chat_completions_ok": r.ok,
                "temperature_used": r.temperature_used,
                "max_token_style": r.max_token_style,
                "repo_default_temperature": temperature_for_model(r.model),
                "note": r.note,
            }
            for r in results
        ]
        print(json.dumps(payload, indent=2))
        return 0

    w = max(len(r.model) for r in results)
    print(f"{'MODEL'.ljust(w)}  OK   temp   max_param           repo_temp_hint  NOTE")
    for r in results:
        hint = str(temperature_for_model(r.model))
        note = r.note.replace("\n", " ")[:60]
        print(
            f"{r.model.ljust(w)}  "
            f"{'yes' if r.ok else 'no ':<4}"
            f"  {r.temperature_used:<6}"
            f"  {r.max_token_style:<20}"
            f"  {hint:<14}"
            f"  {note}"
        )
    ok_n = sum(1 for r in results if r.ok)
    print(
        f"\nSummary: {ok_n}/{len(results)} models accepted a minimal chat completion.",
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Test OpenAI connectivity; list /v1/models; probe /v1/chat/completions.",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Print ids from GET /v1/models (no chat probe).",
    )
    parser.add_argument(
        "--probe-chat",
        action="store_true",
        help=(
            "Probe each candidate model with a tiny chat completion (actual access for Chat Completions)."
        ),
    )
    parser.add_argument(
        "--probe-no-list",
        action="store_true",
        help="With --probe-chat: do not union ids from GET /v1/models (seed/pricing only).",
    )
    parser.add_argument(
        "--probe-no-seed",
        action="store_true",
        help="With --probe-chat: do not union curated seed ids (listed models only, still filtered).",
    )
    parser.add_argument(
        "--probe-timeout",
        type=float,
        default=45.0,
        help="Per-request timeout seconds for each probe (0 disables). Default: 45",
    )
    parser.add_argument(
        "--probe-json",
        action="store_true",
        help="With --probe-chat: print JSON array to stdout (one object per model).",
    )
    args = parser.parse_args()

    if args.list_models and args.probe_chat:
        print("Use only one of --list-models or --probe-chat.", file=sys.stderr)
        sys.exit(2)

    try:
        if args.list_models:
            for m in list_available_models():
                print(m)
        elif args.probe_chat:
            use_list = not args.probe_no_list
            use_seed = not args.probe_no_seed
            if not use_list and not use_seed:
                print(
                    "Refusing to probe with both --probe-no-list and --probe-no-seed.",
                    file=sys.stderr,
                )
                sys.exit(2)
            tout = args.probe_timeout if args.probe_timeout > 0 else None
            sys.exit(
                run_chat_probe(
                    use_list=use_list,
                    use_seed=use_seed,
                    per_request_timeout=tout,
                    json_out=args.probe_json,
                )
            )
        else:
            result = test_llm_connection()
            print(result)
    except ValueError as e:
        print(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"API error: {e}")
        sys.exit(1)
