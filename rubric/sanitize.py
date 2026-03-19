"""Normalize rubric text from LLM outputs (control chars, unicode artifacts)."""


def sanitize_llm_text(text: str) -> str:
    """Normalize malformed control-char artifacts seen in some LLM outputs.

    Some responses contain sequences like "\\x00d7" instead of "×" or
    stray control chars in words. We repair common patterns and drop
    non-whitespace C0 control characters.
    """
    if not text:
        return text

    replacements = {
        "\x00d7": "x",
        "\x00": "",
        "\x19": "'",
        "\u2019": "'",
        "\u2212": "-",
        "\u00d7": "x",
        "\u2248": "~",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    return "".join(c for c in text if ord(c) >= 32 or c in "\n\r\t")
