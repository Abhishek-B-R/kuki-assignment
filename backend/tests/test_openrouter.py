from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm import Blob, PermanentLLMError, TransientLLMError
from app.llm.openrouter import OpenRouterLLM


class Out(BaseModel):
    title: str
    n: int


def client(handler, **kw) -> OpenRouterLLM:
    return OpenRouterLLM("k", "deepseek/deepseek-v4-flash", 5, transport=httpx.MockTransport(handler), **kw)


def ok(content: str, finish: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}]})


def call(llm: OpenRouterLLM):
    return llm.generate(system="sys", parts=[Blob(b"%PDF", "application/pdf"), "page text"], schema=Out, purpose="extraction")


def test_request_shape_and_parse():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return ok('```json\n{"title": "x", "n": 2}\n```')

    out = call(client(handler, reasoning="low", providers=["Parasail", "DeepInfra"], ignore=["Baidu"]))
    assert out == Out(title="x", n=2)
    body = seen["body"]
    assert seen["auth"] == "Bearer k"
    assert body["model"] == "deepseek/deepseek-v4-flash" and body["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    user = body["messages"][1]["content"]
    assert "page text" in user and "%PDF" not in user  # PDF bytes are never sent to a text model
    schema = body["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"title", "n"}  # field named "title" survives
    assert body["provider"] == {"require_parameters": True, "order": ["Parasail", "DeepInfra"], "ignore": ["Baidu"]}
    assert body["reasoning"] == {"effort": "low"}


@pytest.mark.parametrize(
    "resp, transient, rate_limited",
    [
        (httpx.Response(429, headers={"retry-after": "7"}, text="slow down"), True, True),
        (httpx.Response(503, text="overloaded"), True, False),
        (httpx.Response(402, text="insufficient credits"), False, False),
        (httpx.Response(401, text="bad key"), False, False),
        (httpx.Response(200, json={"error": {"code": 502, "message": "upstream"}}), True, False),
        (httpx.Response(200, json={"error": {"code": 400, "message": "bad schema"}}), False, False),
        (ok(""), True, False),
        (ok('{"title": "x"', finish="length"), True, False),
        (ok('{"title": "x"}'), True, False),  # schema mismatch: worth a retry
    ],
)
def test_error_mapping(resp, transient, rate_limited):
    llm = client(lambda _req: resp)
    with pytest.raises(TransientLLMError if transient else PermanentLLMError) as info:
        call(llm)
    if transient:
        assert info.value.rate_limited is rate_limited
    if resp.status_code == 429:
        assert info.value.retry_after == 7


def test_network_error_is_transient():
    def handler(req):
        raise httpx.ConnectError("down", request=req)

    with pytest.raises(TransientLLMError):
        call(client(handler))


def test_missing_key():
    with pytest.raises(PermanentLLMError):
        OpenRouterLLM("", "m", 5)


def test_syntax_slips_are_repaired_but_schema_is_still_enforced():
    llm = client(lambda _req: ok('{"title": "x",\n "n": 2\n "extra": 1}'))  # missing comma
    assert call(llm) == Out(title="x", n=2)
    llm = client(lambda _req: ok('{"title": "x",\n n": 2}'))  # missing opening quote
    assert call(llm) == Out(title="x", n=2)
    with pytest.raises(TransientLLMError):
        call(client(lambda _req: ok('{"title": "x", "n": "not a number"}')))
