"""OpenRouter client (OpenAI-compatible chat completions) with JSON-schema structured output.

Used with text-only models such as deepseek/deepseek-v4-flash. PDF bytes are not sent: the pipeline
always includes the PDF's text layer with [[PAGE n]] markers, so the model reads that instead."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
import json_repair
from pydantic import ValidationError

from .base import Blob, Part, PermanentLLMError, T, TransientLLMError
from .gemini import RateLimiter, json_schema_for

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"
TRANSIENT_CODES = {408, 409, 429, 500, 502, 503, 504, 529}
BLOB_NOTE = "(The original PDF is not attached; read it from the text layer below.)"


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _strip_fences(text: str) -> str:
    m = re.fullmatch(r"\s*```(?:json)?\s*(.*?)\s*```\s*", text, re.S)
    return m.group(1) if m else text


class OpenRouterLLM:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_s: float,
        reasoning: str | None = None,
        rpm: float = 0,
        max_tokens: int = 64000,
        providers: list[str] | None = None,
        ignore: list[str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_key:
            raise PermanentLLMError("OPENROUTER_API_KEY is not set")
        self.name = f"openrouter:{model}"
        self.model = model
        self.reasoning = reasoning
        self.max_tokens = max_tokens
        self.providers = providers or []
        self.ignore = ignore or []
        self.limiter = RateLimiter(rpm)
        self.client = httpx.Client(
            timeout=timeout_s,
            transport=transport,
            headers={"Authorization": f"Bearer {api_key}", "X-Title": "KuKi sustainability assistant"},
        )

    def _body(self, system: str, parts: list[Part], schema: type[T], purpose: str) -> dict[str, Any]:
        text = "\n\n".join(BLOB_NOTE if isinstance(p, Blob) else p for p in parts)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": text}],
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": purpose, "strict": False, "schema": json_schema_for(schema)},
            },
            # Only route to providers that honour response_format; prefer the configured hosts, in order.
            "provider": {
                "require_parameters": True,
                **({"order": self.providers} if self.providers else {}),
                **({"ignore": self.ignore} if self.ignore else {}),
            },
        }
        if self.reasoning:
            body["reasoning"] = {"effort": self.reasoning}
        return body

    def generate(self, *, system: str, parts: list[Part], schema: type[T], purpose: str) -> T:
        self.limiter.wait()
        try:
            resp = self.client.post(URL, json=self._body(system, parts, schema, purpose))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientLLMError(f"OpenRouter network error: {exc}") from exc

        if resp.status_code != 200:
            msg = f"OpenRouter {resp.status_code}: {resp.text[:300]}"
            if resp.status_code in TRANSIENT_CODES:
                raise TransientLLMError(msg, retry_after=_retry_after(resp), rate_limited=resp.status_code == 429)
            raise PermanentLLMError(msg)  # 400 bad request, 401 key, 402 credits, 403 moderation

        try:
            data = resp.json()
        except ValueError as exc:
            raise TransientLLMError("OpenRouter returned a non-JSON body") from exc
        # Upstream provider errors can arrive with HTTP 200.
        if err := data.get("error"):
            code = err.get("code") if isinstance(err, dict) else None
            msg = f"OpenRouter provider error {code}: {err.get('message') if isinstance(err, dict) else err}"
            if code in TRANSIENT_CODES or code is None:
                raise TransientLLMError(msg, rate_limited=code == 429)
            raise PermanentLLMError(msg)

        choice = (data.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason")
        if not text.strip():
            raise TransientLLMError(f"OpenRouter returned no content for {purpose} (finish_reason={finish})")
        if finish == "length":
            raise TransientLLMError(f"OpenRouter {purpose} output was cut off at max_tokens")
        text = _strip_fences(text)
        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            first = exc
        # Some hosts do not enforce the JSON grammar, and a long answer can contain a slip such as a missing
        # comma or quote. Repair the syntax only; the result is validated against the same schema, and every
        # fact is still checked against its source page or cell afterwards.
        try:
            out = schema.model_validate(json_repair.loads(text))
            log.warning("Repaired malformed %s JSON from OpenRouter (%s)", purpose, str(first).splitlines()[0])
            return out
        except (ValidationError, ValueError, TypeError) as exc:
            log.warning("Invalid %s JSON from OpenRouter: %s", purpose, exc)
            raise TransientLLMError(f"OpenRouter returned JSON that does not match the {purpose} schema") from exc

