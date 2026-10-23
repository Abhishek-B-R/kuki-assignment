"""Gemini client (google-genai) with JSON-schema constrained output."""

from __future__ import annotations

import copy
import logging
import re
import threading
import time
from typing import Any

import httpx
from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from .base import Blob, Part, PermanentLLMError, T, TransientLLMError

log = logging.getLogger(__name__)

TRANSIENT_CODES = {408, 409, 429, 500, 502, 503, 504}


def retry_hint(exc: Exception) -> float | None:
    """Seconds the provider asks us to wait: 'retry in 49.8s', 'retry in 20h13.4s', retryDelay '72847s'."""
    text = str(exc)
    m = re.search(r"retry\s+in\s+((?:\d+h)?(?:\d+m(?!s))?(?:\d+(?:\.\d+)?s)?)", text, re.I)
    if m and m.group(1):
        parts = re.findall(r"(\d+(?:\.\d+)?)([hms])", m.group(1))
        return sum(float(v) * {"h": 3600, "m": 60, "s": 1}[u] for v, u in parts)
    m = re.search(r"retryDelay['\"]?\s*:\s*['\"](\d+(?:\.\d+)?)s", text)
    return float(m.group(1)) if m else None


class RateLimiter:
    """Spaces calls at least 60/rpm seconds apart across all threads of the process."""

    def __init__(self, rpm: float):
        self.interval = 60.0 / rpm if rpm > 0 else 0.0
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        if not self.interval:
            return
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + self.interval
        if start > now:
            time.sleep(start - now)


def json_schema_for(model: type) -> dict[str, Any]:
    """Pydantic JSON schema with $refs inlined and keys Gemini ignores removed."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            out = {}
            for k, v in node.items():
                if k == "properties":
                    # Keys here are field names (a field may be called "title"), not schema keywords.
                    out[k] = {name: resolve(sub) for name, sub in v.items()}
                elif k not in ("title", "default"):
                    out[k] = resolve(v)
            return out
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


class GeminiLLM:
    def __init__(self, api_key: str, model: str, timeout_s: float, thinking: str | None = "low", rpm: float = 0):
        if not api_key:
            raise PermanentLLMError("GEMINI_API_KEY is not set")
        self.name = f"gemini:{model}"
        self.model = model
        self.thinking = thinking
        self.limiter = RateLimiter(rpm)
        self.client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    def generate(self, *, system: str, parts: list[Part], schema: type[T], purpose: str) -> T:
        contents: list[Any] = [
            types.Part.from_bytes(data=p.data, mime_type=p.mime_type) if isinstance(p, Blob) else types.Part.from_text(text=p)
            for p in parts
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=json_schema_for(schema),
            temperature=0,
            thinking_config=types.ThinkingConfig(thinking_level=self.thinking) if self.thinking else None,
        )
        self.limiter.wait()
        try:
            resp = self.client.models.generate_content(model=self.model, contents=contents, config=config)
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            msg = f"Gemini {code}: {getattr(exc, 'message', None) or exc}"
            if code in TRANSIENT_CODES:
                raise TransientLLMError(msg, retry_after=retry_hint(exc), rate_limited=code == 429) from exc
            raise PermanentLLMError(msg) from exc
        except (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError) as exc:
            raise TransientLLMError(f"Gemini network error: {exc}") from exc

        text = resp.text
        if not text:
            reason = None
            if resp.candidates:
                reason = resp.candidates[0].finish_reason
            # An empty answer is usually a hiccup (or MAX_TOKENS); safety blocks are permanent.
            if reason is not None and "SAFETY" in str(reason):
                raise PermanentLLMError(f"Gemini blocked the {purpose} response ({reason})")
            raise TransientLLMError(f"Gemini returned no content for {purpose} (finish_reason={reason})")
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            log.warning("Invalid %s JSON from Gemini: %s", purpose, exc)
            raise TransientLLMError(f"Gemini returned JSON that does not match the {purpose} schema") from exc
