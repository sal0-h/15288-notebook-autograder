"""Tests for prompt_builder token estimation behavior."""

import prompt_builder


class _FakeEncoding:
    def __init__(self, value: int):
        self.value = value

    def encode(self, text: str):
        return [0] * self.value


class _FakeTikToken:
    def encoding_for_model(self, model: str):
        if model == "model-a":
            return _FakeEncoding(10)
        if model == "model-b":
            return _FakeEncoding(20)
        raise KeyError(model)

    def get_encoding(self, _name: str):
        return _FakeEncoding(30)


def test_estimate_tokens_caches_per_model(monkeypatch):
    monkeypatch.setattr(prompt_builder, "_enc_cache", {})
    monkeypatch.setattr(prompt_builder, "tiktoken", _FakeTikToken())

    a = prompt_builder.estimate_tokens("x", 0, model="model-a")
    b = prompt_builder.estimate_tokens("x", 0, model="model-b")

    assert a == 10
    assert b == 20
