"""Shared types for LLM completion results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def has_tokens(self) -> bool:
        return bool(self.prompt_tokens or self.completion_tokens)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def merged(self, other: TokenUsage) -> TokenUsage:
        """Return counts combined with another usage record (immutable)."""
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )

    @classmethod
    def from_json_dict(cls, data: Mapping[str, Any] | None) -> TokenUsage:
        """Parse counts from JSON (same keys as fields — usual OpenAI-style usage shape)."""
        if not data:
            return cls()
        return cls(
            prompt_tokens=int(data.get("prompt_tokens", 0) or 0),
            completion_tokens=int(data.get("completion_tokens", 0) or 0),
        )

    def to_json_dict(self) -> dict[str, int]:
        """Serialize for JSON; keys match attribute names (``_usage``, SSE, estimates)."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass(frozen=True)
class JsonCompletionResult:
    """Normalized output from a chat completion expecting JSON."""

    text: str
    usage: TokenUsage
