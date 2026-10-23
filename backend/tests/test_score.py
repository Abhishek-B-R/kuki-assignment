from __future__ import annotations

from pathlib import Path

import yaml

from evals.score import score_case, summarise

CASES = yaml.safe_load((Path(__file__).resolve().parents[1] / "evals" / "cases.yaml").read_text())


def test_cases_file_is_well_formed():
    assert len(CASES) >= 10
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    statuses = {"answered", "partial", "not_found", "conflicting"}
    for c in CASES:
        assert c["question"] and c["expected_answer"] and c["sources"]
        assert set(c["expected_status"]) <= statuses
        assert c["documents"] == "all" or all((Path(__file__).resolve().parents[2] / "dataset" / d).exists() for d in c["documents"])


def answer(**kw):
    base = {"status": "answered", "answer": "", "key_values": [], "calculations": [], "citations": [], "gaps": [],
            "inconsistencies": [], "assumptions": []}
    base.update(kw)
    return base


WATER = next(c for c in CASES if c["id"] == "C02")


def cite(filename, page, verification="verified"):
    return {"filename": filename, "page": page, "verification": verification}


def test_perfect_answer_scores_one():
    a = answer(
        answer="Yes: 14,821 - 12,500 = 2,321 m³, matching the billed 2,321.",
        calculations=[{"expression": "14821 - 12500", "result": 2321, "ok": True}],
        citations=[cite("water_bill_invoice.pdf", 1)],
    )
    s = score_case(WATER, a)
    assert s["overall"] == 1.0 and s["passed"]


def test_wrong_status_missing_values_and_bad_citations_are_penalised():
    a = answer(
        status="partial",
        answer="It is 2,321.",
        calculations=[{"expression": "1+1", "result": 3, "ok": False}],
        citations=[cite("water_bill_invoice.pdf", 1, "unverified")],
    )
    s = score_case(WATER, a)
    assert s["scores"]["answer"] == round(1 / 3, 4)
    assert s["scores"]["status"] == 0 and s["scores"]["evidence"] == 0 and s["scores"]["calculation"] == 0
    assert not s["passed"]


def test_must_not_and_sheet_ranges():
    policy = next(c for c in CASES if c["id"] == "C05")
    s = score_case(policy, answer(answer="Complaints are resolved within 30 days. It does not specify more."))
    assert s["scores"]["answer"] == 0 and not s["passed"]
    wb = next(c for c in CASES if c["id"] == "C04")
    a = answer(
        status="conflicting",
        answer="Stated grid 1,533,290 vs summed 1,534,290: a 1,000 kWh gap.",
        citations=[{"filename": "Annual Electricity Consumption.xlsx", "sheet": "Monthly Summary 2026", "cell_range": "B16",
                    "verification": "verified"}],
    )
    s = score_case(wb, a)
    assert s["passed"] and s["scores"]["evidence"] == 1.0


def test_summarise():
    rows = [{"score": score_case(WATER, answer())}, {"score": score_case(WATER, answer(answer="2321 14821 12500"))}]
    out = summarise(rows)
    assert out["cases"] == 2 and 0 <= out["overall"] <= 1


def test_typographic_dashes_match_ascii_alternatives():
    case = {"must_include": [["end-to-end"]], "must_flag": [["jan-mar 2027"]], "expected_status": ["answered"], "sources": []}
    s = score_case(case, answer(answer="Quality is End‑to‑End.", gaps=["Jan–Mar 2027 missing"]))
    assert s["scores"]["answer"] == 1.0 and s["scores"]["uncertainty"] == 1.0
