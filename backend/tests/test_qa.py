from __future__ import annotations

from fastapi.testclient import TestClient

from app.api import create_app
from app.llm.fake import FakeLLM
from app.worker import Worker

from .test_e2e import BESCOM, WORKBOOK, extraction_for, settled, text_of, upload, wait_for


def answer_with(captured: dict, **over):
    def handler(system, parts):
        captured["system"], captured["prompt"] = system, parts[0]
        ans = {
            "status": "answered",
            "answer": "63,000 kWh for 1 June to 1 July 2024.",
            "key_values": [{"label": "Net consumption", "value": "63000", "unit": "kWh", "origin": "reported", "citation_indexes": [0]}],
            "calculations": [
                {"description": "meter", "expression": "(639.25 - 623.5) x 4000", "result": 63000, "unit": "kWh"},
                {"description": "wrong", "expression": "1 + 1", "result": 3, "unit": None},
            ],
            "citations": [
                {"document_id": "D1", "page": None, "sheet": None, "cell_range": None, "fact_id": "F2", "quote": "Amount : 808528.00"},
                {"document_id": "D1", "page": 1, "sheet": None, "cell_range": None, "fact_id": None, "quote": "Net Payable Amount 808528.00"},
                {"document_id": "D1", "page": 1, "sheet": None, "cell_range": None, "fact_id": None, "quote": "Total energy 12345 kWh"},
                {"document_id": "D9", "page": 1, "sheet": None, "cell_range": None, "fact_id": None, "quote": "x"},
            ],
            "gaps": [], "inconsistencies": [], "assumptions": [],
        }
        ans.update(over)
        return ans

    return handler


def env(settings, answer_handler):
    llm = FakeLLM({"extraction": lambda _s, parts: extraction_for(text_of(parts)), "answer": answer_handler})
    client = TestClient(create_app(settings, llm=llm))
    client.__enter__()
    worker = Worker(settings, llm=llm)
    worker.start()
    return client, worker


def test_answer_citations_and_calculations_are_verified(settings):
    captured: dict = {}
    client, worker = env(settings, answer_with(captured))
    try:
        res = upload(client, BESCOM, WORKBOOK)
        wait_for(client, settled)
        bescom_id = res[0]["document"]["id"]
        r = client.post("/api/ask", json={"question": "What is the net consumption?", "document_ids": [bescom_id]})
        assert r.status_code == 200, r.text
        out = r.json()
        assert [d["id"] for d in out["documents"]] == [bescom_id]  # selection respected
        assert "Annual Electricity" not in captured["prompt"]
        cites = out["citations"]
        assert cites[0]["fact_id"] and cites[0]["page"] == 1 and cites[0]["verification"] == "verified"
        assert cites[1]["verification"] == "verified"
        assert cites[2]["verification"] == "unverified"
        assert cites[3]["verification"] == "unverified" and cites[3]["note"] == "unknown document"
        assert [c["ok"] for c in out["calculations"]] == [True, False]
        assert out["support"] == {"citations": 4, "verified": 2, "partial": 0, "unverified": 2,
                                  "calculations_ok": 1, "calculations": 2}
        assert out["fy_report"] is None
    finally:
        worker.shutdown()


def test_fy_question_gets_computed_report_in_evidence(settings):
    captured: dict = {}
    client, worker = env(settings, answer_with(captured, status="partial"))
    try:
        upload(client, BESCOM, WORKBOOK)
        wait_for(client, settled)
        out = client.post("/api/ask", json={"question": "Total electricity in FY 2024-25?"}).json()
        assert out["financial_year"] == "FY 2024-25"
        assert "Financial-year report computed by our software for FY 2024-25" in captured["prompt"]
        assert "Financial year: FY 2024-25 (2024-04-01 to 2025-03-31)" in captured["prompt"]
        assert "answer for that year only" in captured["system"]
        assert out["fy_report"]["groups"][0]["total"] == 63000
        # explicit FY overrides the question text
        out = client.post("/api/ask", json={"question": "Total electricity?", "financial_year": "FY2025-26"}).json()
        assert out["financial_year"] == "FY 2025-26"
    finally:
        worker.shutdown()


def test_ask_validation(settings):
    client, worker = env(settings, answer_with({}))
    try:
        assert client.post("/api/ask", json={"question": "  "}).status_code == 400
        out = client.post("/api/ask", json={"question": "anything"}).json()
        assert out["status"] == "not_found" and "processed" in out["answer"]
    finally:
        worker.shutdown()
