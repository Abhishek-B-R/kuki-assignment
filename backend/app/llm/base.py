"""Provider-neutral interface for structured (JSON) generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """Base class. `transient` tells the job queue whether a retry can help."""

    transient = False


class TransientLLMError(LLMError):
    """Rate limits, timeouts, 5xx, network trouble, or malformed output worth retrying.

    `retry_after` is the provider's own hint (seconds). `rate_limited` marks quota/429 errors:
    the job is deferred rather than charged an attempt, because nothing is wrong with it."""

    transient = True

    def __init__(self, message: str, retry_after: float | None = None, rate_limited: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.rate_limited = rate_limited


class PermanentLLMError(LLMError):
    """Bad request, missing key, content the model refuses. Retrying will not help."""


@dataclass(frozen=True)
class Blob:
    data: bytes
    mime_type: str


Part = str | Blob


class LLM(Protocol):
    name: str

    def generate(self, *, system: str, parts: list[Part], schema: type[T], purpose: str) -> T:
        """Return a validated instance of `schema`. `purpose` labels the call (logs, fakes)."""
        ...
