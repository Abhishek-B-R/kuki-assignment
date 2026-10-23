"""Scripted LLM for tests: responses are produced by a handler per call purpose."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import Part, T


class FakeLLM:
    name = "fake"

    def __init__(self, handlers: dict[str, Callable[[str, list[Part]], Any]] | None = None):
        self.handlers = handlers or {}
        self.calls: list[tuple[str, str, list[Part]]] = []

    def generate(self, *, system: str, parts: list[Part], schema: type[T], purpose: str) -> T:
        self.calls.append((purpose, system, parts))
        handler = self.handlers.get(purpose)
        if handler is None:
            raise AssertionError(f"FakeLLM has no handler for {purpose!r}")
        result = handler(system, parts)  # may raise, to simulate failures
        if isinstance(result, schema):
            return result
        return schema.model_validate(result)
