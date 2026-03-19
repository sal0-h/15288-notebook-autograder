"""Single entry point for JSON-shaped chat completions."""

from __future__ import annotations

from openai import OpenAI

from llm.types import JsonCompletionResult, TokenUsage
from utils import temperature_for_model


def complete_json_chat(
    client: OpenAI,
    *,
    model: str,
    messages: list,
    max_completion_tokens: int,
) -> JsonCompletionResult:
    """Call chat.completions with json_object format; return text and token usage."""
    temperature = temperature_for_model(model)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
        max_completion_tokens=max_completion_tokens,
        response_format={"type": "json_object"},
    )
    raw_content = response.choices[0].message.content
    text = raw_content or "{}"
    usage = TokenUsage()
    if getattr(response, "usage", None):
        usage = TokenUsage(
            prompt_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
        )
    return JsonCompletionResult(text=text, usage=usage)
