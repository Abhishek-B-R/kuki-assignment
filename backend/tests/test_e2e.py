"""Upload -> background worker -> results, through the real API, queue and pipeline.
Only the model is faked."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import jobqueue
from app.api import create_app
from app.db import connect
from app.llm import PermanentLLMError, TransientLLMError
from app.llm.fake import FakeLLM
from app.worker import Worker

from .conftest import DATASET

BESCOM = DATASET / "BESCOM Commercial Electricity Bill.pdf"
WATER = DATASET / "water_bill_invoice.pdf"
WORKBOOK = DATASET / "Annual Electricity Consumption.xlsx"


def fact(id, key, value_text, value_num, page=1, quote=None, **kw):
    return {
        "id": id, "key": key, "label": key, "category": kw.pop("category", "consumption"), "metric": kw.pop("metric", None),
        "dimension": kw.pop("dimension", None), "primary": kw.pop("primary", True), "value_text": value_text,
        "value_num": value_num, "unit": kw.pop("unit", None), "period_start": kw.pop("period_start", None),
        "period_end": kw.pop("period_end", None), "page": page, "sheet": kw.pop("sheet", None),
        "cell_range": kw.pop("cell_range", None), "quote": quote if quote is not None else value_text, "notes": None,
    }


def extraction_for(text: str) -> dict:
    base = {"title": "t", "issuer": None, "summary": "s", "reporting_periods": [], "relations": [], "gaps": [], "anomalies": []}
    if "BESCOM" in text:
        return {
            **base,
            "doc_type": "electricity_bill",
            "organisation": "Prestige Estates Projects Pvt Ltd",
            "facts": [
                fact("f1", "net_consumption_kwh", "63000", 63000, metric="electricity_consumption", dimension="total",
                     unit="kWh", period_start="2024-06-01", period_end="2024-07-01", quote="Net  Consumption"),
                fact("f2", "net_payable_amount", "808528.00", 808528.0, category="financial", metric="amount_payable",
                     unit="INR", quote="Amount : 808528.00"),
                fact("f3", "invented_amount", "999999.00", 999999.0, category="financial", quote="Grand total 999999.00"),
                fact("f4", "kwh_present", "639.25", 639.25, category="meter_reading"),
                fact("f5", "kwh_previous", "623.5", 623.5, category="meter_reading"),
                fact("f6", "meter_constant", "4000", 4000, category="meter_reading"),
            ],
            "relations": [
                {"description": "meter", "operation": "difference", "target": "f1", "operands": ["f4", "f5"], "factor": 4000},
            ],
        }
    if "Spreadsheet cells" in text:
        return {
            **base,
            "doc_type": "consumption_workbook",
            "organisation": None,
            "facts": [
                fact("f1", "january_total_kwh", "200823", 200823, page=None, sheet="Monthly Summary 2026", cell_range="F3",
                     metric="electricity_consumption", dimension="total", unit="kWh",
                     period_start="2026-01-01", period_end="2026-01-31"),
            ],
        }
    return {**base, "doc_type": "water_bill", "organisation": "Coca-Cola", "facts": []}


def text_of(parts) -> str:
    return "\n".join(p for p in parts if isinstance(p, str))


@pytest.fixture
def make_env(settings):
    started: list[Worker] = []

    def make(handler=None, start_worker=True):
        llm = FakeLLM({"extraction": handler or (lambda _s, parts: extraction_for(text_of(parts)))})
        client = TestClient(create_app(settings, llm=llm))
        client.__enter__()
        worker = Worker(settings, llm=llm)
        if start_worker:
            worker.start()
            started.append(worker)
        return client, worker, llm

    yield make
    for w in started:
        w.shutdown()


def upload(client, *paths):
    files = [("files", (p.name, p.read_bytes())) for p in paths]
    r = client.post("/api/documents", files=files)
    assert r.status_code == 200, r.text
    return r.json()


def wait_for(client, predicate, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        docs = client.get("/api/documents").json()["documents"]
        if predicate(docs):
            return docs
        time.sleep(0.05)
    raise AssertionError(f"timed out; last state: {[(d['filename'], d['status'], d['error']) for d in docs]}")


def settled(docs):
    return docs and all(d["status"] in ("succeeded", "failed") for d in docs)


def test_batch_upload_processes_each_document_independently(make_env, tmp_path):
    client, _, _ = make_env()
    corrupt = tmp_path / "corrupt.pdf"
    corrupt.write_bytes(b"%PDF-1.4 truncated garbage")
    notes = tmp_path / "notes.docx"
    notes.write_bytes(b"PK\x03\x04 not supported")
    res = upload(client, BESCOM, WORKBOOK, corrupt, notes)
    assert [r["duplicate"] for r in res] == [False] * 4

    docs = {d["filename"]: d for d in wait_for(client, settled)}
    assert docs[BESCOM.name]["status"] == "succeeded"
    assert docs[WORKBOOK.name]["status"] == "succeeded"
    assert docs["corrupt.pdf"]["status"] == "failed" and "Cannot open PDF" in docs["corrupt.pdf"]["error"]
    assert docs["notes.docx"]["status"] == "failed" and "Unsupported file type" in docs["notes.docx"]["error"]
    assert docs["corrupt.pdf"]["attempts"] == 1  # permanent errors are not retried

    detail = client.get(f"/api/documents/{docs[BESCOM.name]['id']}").json()
    facts = {f["key"]: f for f in detail["facts"]}
    assert all(f["origin"] == "reported" for f in facts.values())
    assert facts["net_payable_amount"]["evidence_status"] == "verified"
    assert facts["net_payable_amount"]["bbox"]
    assert facts["invented_amount"]["evidence_status"] == "unverified"  # not on the page
    (meter,) = detail["checks"]
    assert meter["status"] == "pass" and meter["expected"] == pytest.approx(63000)
    assert [e["message"] for e in detail["events"]][-1] == "Processing succeeded"

    wb = client.get(f"/api/documents/{docs[WORKBOOK.name]['id']}").json()
    assert sum(c["status"] == "fail" for c in wb["checks"]) == 4  # table totals run on every workbook
    assert wb["facts"][0]["evidence_status"] == "verified"


def test_duplicate_upload_is_not_processed_twice(make_env):
    client, _, llm = make_env()
    first = upload(client, BESCOM)[0]
    second = upload(client, BESCOM, BESCOM)
    assert all(r["duplicate"] for r in second)
    assert {r["document"]["id"] for r in second} == {first["document"]["id"]}
    wait_for(client, settled)
    assert len(client.get("/api/documents").json()["documents"]) == 1
    assert sum(1 for c in llm.calls if c[0] == "extraction") == 1


def test_transient_model_errors_are_retried_then_succeed(make_env):
    calls = {"n": 0}

    def flaky(_s, parts):
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientLLMError("Gemini 429: rate limited")
        return extraction_for(text_of(parts))

    client, _, _ = make_env(flaky)
    upload(client, BESCOM)
    (doc,) = wait_for(client, settled)
    assert doc["status"] == "succeeded" and doc["attempts"] == 3
    events = [e["message"] for e in client.get(f"/api/documents/{doc['id']}").json()["events"]]
    assert sum("Temporary failure" in m for m in events) == 2


def test_persistent_transient_errors_give_up(make_env):
    def always(_s, _p):
        raise TransientLLMError("Gemini 503: overloaded")

    client, _, _ = make_env(always)
    upload(client, BESCOM)
    (doc,) = wait_for(client, settled)
    assert doc["status"] == "failed" and doc["attempts"] == 3
    assert "Gave up after 3 attempts" in doc["error"]


def test_permanent_model_error_fails_one_document_only(make_env):
    def picky(_s, parts):
        if "BESCOM" in text_of(parts):
            raise PermanentLLMError("Gemini 400: invalid argument")
        return extraction_for(text_of(parts))

    client, _, _ = make_env(picky)
    upload(client, BESCOM, WATER)
    docs = {d["filename"]: d for d in wait_for(client, settled)}
    assert docs[BESCOM.name]["status"] == "failed" and docs[BESCOM.name]["attempts"] == 1
    assert docs[WATER.name]["status"] == "succeeded"


def test_retry_after_failure_and_reprocess_are_idempotent(make_env):
    state = {"fail": True}

    def toggle(_s, parts):
        if state["fail"]:
            raise PermanentLLMError("bad")
        return extraction_for(text_of(parts))

    client, _, _ = make_env(toggle)
    doc_id = upload(client, BESCOM)[0]["document"]["id"]
    wait_for(client, settled)
    state["fail"] = False
    again = upload(client, BESCOM)[0]  # re-uploading a failed file retries it
    assert again["duplicate"] and again["requeued"]
    wait_for(client, lambda d: d[0]["status"] == "succeeded")
    n_facts = len(client.get(f"/api/documents/{doc_id}").json()["facts"])
    for _ in range(2):
        r = client.post(f"/api/documents/{doc_id}/reprocess")
        assert r.status_code == 200
        wait_for(client, lambda d: d[0]["status"] == "succeeded")
    detail = client.get(f"/api/documents/{doc_id}").json()
    assert len(detail["facts"]) == n_facts  # replaced, never duplicated
    assert len(detail["checks"]) == 1


def test_reprocess_while_queued_is_rejected(make_env):
    client, _, _ = make_env(start_worker=False)
    doc_id = upload(client, BESCOM)[0]["document"]["id"]
    assert client.post(f"/api/documents/{doc_id}/reprocess").status_code == 409


def test_crashed_worker_job_is_recovered_and_results_survive_restart(make_env, settings):
    client, _, _ = make_env(start_worker=False)
    doc_id = upload(client, BESCOM)[0]["document"]["id"]
    # A worker claims the job and dies without a trace (no heartbeat, no outcome).
    conn = connect(settings.db_path)
    dead = jobqueue.claim(conn, "dead-worker", lease_s=0.2)
    assert dead is not None
    conn.close()
    client.__exit__(None, None, None)

    # "Restart": a fresh API and worker over the same database.
    client2, _, _ = make_env()
    (doc,) = wait_for(client2, settled)
    assert doc["status"] == "succeeded" and doc["attempts"] == 2
    events = [e["message"] for e in client2.get(f"/api/documents/{doc_id}").json()["events"]]
    assert any("reclaimed" in m for m in events)
    client2.__exit__(None, None, None)

    client3, _, _ = make_env(start_worker=False)
    detail = client3.get(f"/api/documents/{doc_id}").json()
    assert detail["document"]["status"] == "succeeded" and detail["facts"]


def test_pages_sheets_and_files_are_served(make_env):
    client, _, _ = make_env()
    res = upload(client, BESCOM, WORKBOOK)
    wait_for(client, settled)
    pdf_id, wb_id = res[0]["document"]["id"], res[1]["document"]["id"]
    r = client.get(f"/api/documents/{pdf_id}/pages/1.png")
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert client.get(f"/api/documents/{pdf_id}/pages/9.png").status_code == 404
    sheet = client.get(f"/api/documents/{wb_id}/sheets/Monthly Summary 2026").json()
    assert sheet["rows"] == 16 and any(c["ref"] == "F16" for c in sheet["cells"])
    assert client.get(f"/api/documents/{pdf_id}/file").content[:4] == b"%PDF"
    assert client.delete(f"/api/documents/{pdf_id}").status_code == 200
    assert client.get(f"/api/documents/{pdf_id}").status_code == 404


def test_fy_endpoint_uses_processed_facts(make_env):
    client, _, _ = make_env()
    upload(client, BESCOM, WORKBOOK)
    wait_for(client, settled)
    assert client.get("/api/fy").json()["years"] == ["FY 2024-25", "FY 2025-26"]
    rep = client.get("/api/fy/FY2024-25").json()
    (g,) = [g for g in rep["groups"] if g["metric"] == "electricity_consumption"]
    assert g["organisation"].startswith("Prestige") and g["total"] == 63000
    assert client.get("/api/fy/nonsense").status_code == 400


def test_responses_never_expose_server_paths(make_env):
    client, _, _ = make_env(start_worker=False)
    (res,) = upload(client, BESCOM)
    assert "path" not in res["document"]
    assert "path" not in client.get(f"/api/documents/{res['document']['id']}").json()["document"]
    assert all("path" not in d for d in client.get("/api/documents").json()["documents"])


def test_reverify_recomputes_evidence_offline(make_env, settings):
    from app.reverify import reverify

    client, _, llm = make_env()
    doc_id = upload(client, BESCOM)[0]["document"]["id"]
    wait_for(client, settled)
    conn = connect(settings.db_path)
    conn.execute("UPDATE facts SET evidence_status='unverified', bbox_json=NULL WHERE document_id=?", (doc_id,))
    conn.execute("DELETE FROM checks WHERE document_id=?", (doc_id,))
    conn.close()
    calls = len(llm.calls)
    counts = reverify(settings.db_path)[BESCOM.name]
    assert len(llm.calls) == calls  # no model calls
    assert counts["verified"] >= 5 and counts["unverified"] == 1  # only the invented amount stays unsupported
    detail = client.get(f"/api/documents/{doc_id}").json()
    assert len(detail["checks"]) == 1 and detail["checks"][0]["status"] == "pass"


def test_v2_extraction_expands_spreadsheet_series(make_env, settings):
    import dataclasses

    from app.api import create_app
    from app.worker import Worker

    def sheet_v2(system, parts):
        assert "Do NOT list per-row" in system
        out = extraction_for(text_of(parts))
        out["series"] = [{
            "key": "total_kwh", "header": "Total Consumption (kWh)", "sheet": "Monthly Summary 2026", "label_column": "A",
            "value_column": "F", "first_row": 3, "last_row": 14, "metric": "electricity_consumption", "dimension": "total",
            "unit": "kWh", "year": 2026, "quarter_basis": "calendar", "year_note": "sheet title",
        }]
        return out

    v2 = dataclasses.replace(settings, extract_version="v2")
    llm = FakeLLM({"extraction": sheet_v2})
    client = TestClient(create_app(v2, llm=llm))
    with client:
        worker = Worker(v2, llm=llm)
        worker.start()
        try:
            doc_id = upload(client, WORKBOOK)[0]["document"]["id"]
            wait_for(client, settled)
            detail = client.get(f"/api/documents/{doc_id}").json()
            assert detail["document"]["pipeline_version"] == "extract-v2"
            months = [f for f in detail["facts"] if f["local_id"].startswith("s")]
            assert len(months) == 12 and all(f["evidence_status"] == "verified" for f in months)
            rep = client.get("/api/fy/FY2025-26").json()
            assert rep["groups"][0]["total"] == 584323
        finally:
            worker.shutdown()
