from __future__ import annotations

import dataclasses
import json

from fastapi.testclient import TestClient

from app import api
from app.llm.fake import FakeLLM


def run(name: str, passed: int) -> dict:
    return {"name": name, "ran_at": "2026-10-23T00:00:00+00:00", "summary": {"cases": 16, "passed": passed}, "cases": []}


def test_evals_lists_main_runs_and_repeat_summaries(settings, tmp_path, monkeypatch):
    results = tmp_path / "results"
    results.mkdir()
    for name, passed in (("baseline", 6), ("baseline-r2", 5), ("improved", 9), ("improved-r2", 10), ("improved-r3", 9)):
        (results / f"{name}.json").write_text(json.dumps(run(name, passed)))
    monkeypatch.setattr(api, "EVAL_RESULTS", results)
    with TestClient(api.create_app(settings, llm=FakeLLM({}))) as client:
        out = client.get("/api/evals").json()
    assert out["runs"]["baseline"]["summary"]["passed"] == 6
    assert [r["summary"]["passed"] for r in out["repeats"]["baseline"]] == [5]
    assert [r["name"] for r in out["repeats"]["improved"]] == ["improved-r2", "improved-r3"]
    assert "cases" not in out["repeats"]["improved"][0]
    assert len(out["cases"]) >= 10


def test_health_names_the_configured_provider(settings):
    s = dataclasses.replace(settings, llm_provider="openrouter", openrouter_model="deepseek/deepseek-v4-flash")
    with TestClient(api.create_app(s, llm=FakeLLM({}))) as client:
        assert client.get("/api/health").json()["model"] == "openrouter:deepseek/deepseek-v4-flash"
