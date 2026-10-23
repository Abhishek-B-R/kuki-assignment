from __future__ import annotations

from pathlib import Path

import pytest

from app.checks import classify, evaluate_relation, table_total_checks
from app.numbers import normalise_unit, numbers_in, parse_number
from app.parsing.pdf import PdfError, find_boxes, parse_pdf
from app.parsing.sheets import parse_csv, parse_xlsx, split_ref
from app.qa import safe_eval
from app.schemas import ExtractedFact, Relation
from app.verify import verify_pdf, verify_sheet

from .conftest import DATASET

WORKBOOK = DATASET / "Annual Electricity Consumption.xlsx"
BESCOM = DATASET / "BESCOM Commercial Electricity Bill.pdf"


@pytest.mark.parametrize(
    "text,value",
    [
        ("₹1,51,174.88", 151174.88),
        ("6,31,510.00", 631510.0),
        ("r 631510.00*", 631510.0),
        ("-8,777.32", -8777.32),
        ("34.0%", 34.0),
        ("1.0 T", 1.0),
        ("2,321 Cubic Meters", 2321.0),
        ("Jan - Mar", None),
        ("01-06-2024 - 01-07-2024", None),  # several numbers: not a single value
        ("", None),
    ],
)
def test_parse_number(text, value):
    assert parse_number(text) == value


def test_numbers_in_and_units():
    assert numbers_in("Previous 3539.68 Present 4132.27 MF 80") == [3539.68, 4132.27, 80.0]
    assert normalise_unit("Cubic Meters") == ("m3", 1.0)
    assert normalise_unit("MWh") == ("kWh", 1000.0)
    assert normalise_unit("MT") == ("t", 1.0)
    assert normalise_unit("kg") == ("t", 0.001)
    assert normalise_unit("widgets") == ("widgets", 1.0)


def test_split_ref():
    assert split_ref("'Monthly Summary 2026'!B3:B14") == ("Monthly Summary 2026", "B3:B14")
    assert split_ref("Monthly Trends & Comparison!$B$2") == ("Monthly Trends & Comparison", "B2")
    assert split_ref("B16", "Sheet1") == ("Sheet1", "B16")
    assert split_ref("not a ref") is None


def test_parse_pdf_pages_and_boxes():
    pages = parse_pdf(BESCOM)
    assert len(pages) == 2
    assert "808528.00" in pages[0].text
    assert pages[1].word_count == 0  # image-only poster page
    boxes = find_boxes(BESCOM, 1, ["Net Payable Amount"])
    assert boxes and all(0 <= v <= 1 for v in boxes[0])


def test_parse_pdf_rejects_garbage(tmp_path: Path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"this is not a pdf")
    with pytest.raises(PdfError):
        parse_pdf(bad)


def test_parse_xlsx_and_csv(tmp_path: Path):
    cells = parse_xlsx(WORKBOOK)
    jan = next(c for c in cells if c.sheet == "Monthly Summary 2026" and c.ref == "B3")
    assert jan.number == 132590
    pct = next(c for c in cells if c.sheet == "Monthly Summary 2026" and c.ref == "G3")
    assert pct.value == "34.0%"
    csv_path = tmp_path / "m.csv"
    csv_path.write_text("Month,kWh\nJan,10\nFeb,20\nTotal,30\n")
    cells = parse_csv(csv_path)
    assert [c.number for c in cells if c.col == 2] == [None, 10, 20, 30]
    assert table_total_checks(cells)[0].status == "pass"


def test_verify_pdf_quote_value_and_page_correction():
    pages = {p.page_no: p.text for p in parse_pdf(BESCOM)}
    assert verify_pdf(pages, 1, "Net Payable Amount 808528.00", 808528.0).status == "verified"
    # The model pointed at the wrong page: we find it, correct it, and downgrade to partial.
    ev = verify_pdf(pages, 2, "Net Payable Amount 808528.00", 808528.0)
    assert (ev.status, ev.page) == ("partial", 1)
    # A value that does not appear in its own quote is rejected.
    assert verify_pdf(pages, 1, "Net Payable Amount 808528.00", 808527.8).status == "unverified"
    assert verify_pdf(pages, 1, "Total Amount 123456.00", 123456.0).status == "unverified"


def test_verify_sheet():
    cells = parse_xlsx(WORKBOOK)
    assert verify_sheet(cells, "Monthly Summary 2026", "F16", "2425123", 2425123).status == "verified"
    assert verify_sheet(cells, "Monthly Summary 2026", "A3:K3", "132590", 132590).status == "verified"
    assert verify_sheet(cells, "Monthly Summary 2026", "F16", "2426123", 2426123).status == "unverified"
    assert verify_sheet(cells, "Nope", "F16", "1", 1).status == "unverified"
    assert verify_sheet(cells, "Monthly Summary 2026", "K16", "Full year 2026", None).status == "verified"


def test_table_totals_find_the_workbook_errors():
    results = {(r.name.split(":")[0], r.details["target_ref"][0]): r for r in table_total_checks(parse_xlsx(WORKBOOK))}
    fails = sorted(ref for (_, ref), r in results.items() if r.status == "fail")
    assert fails == [
        "Monthly Summary 2026!B16",
        "Monthly Summary 2026!F16",
        "Monthly Summary 2026!H16",
        "Monthly Summary 2026!J16",
    ]
    grid = results[("Monthly Summary 2026", "Monthly Summary 2026!B16")]
    assert (grid.expected, grid.actual, grid.difference) == (1534290, 1533290, -1000)
    # Percentage and per-unit columns are never summed; subtotal rows are skipped.
    assert not any("%" in ref for _, ref in results)
    src_total = results[("Source-wise Annual Analysis", "Source-wise Annual Analysis!B7")]
    assert src_total.status == "pass" and src_total.details["operand_refs"] == ["Source-wise Annual Analysis!B2:B4"]


def _fact(id, value, text, **kw):
    base = dict(key=id, label=id, category="other", metric=None, dimension=None, primary=True, unit=None,
                period_start=None, period_end=None, page=1, sheet=None, cell_range=None, quote=text, notes=None)
    base.update(kw)
    return ExtractedFact(id=id, value_text=text, value_num=value, **base)


def test_relation_meter_reading_rounding_and_sum_with_credits():
    facts = {
        "f1": _fact("f1", 4132.27, "4132.27"),
        "f2": _fact("f2", 3539.68, "3539.68"),
        "f3": _fact("f3", 47407, "47407"),
        "a": _fact("a", 100.0, "100.00"),
        "b": _fact("b", -12.5, "12.50Cr"),
        "t": _fact("t", 87.5, "87.50"),
    }
    meter = evaluate_relation(
        Relation(description="meter", operation="difference", target="f3", operands=["f1", "f2"], factor=80), facts, []
    )
    assert meter.status == "pass" and meter.expected == pytest.approx(47407.2)  # equal at the stated precision
    facts.update(p1=_fact("p1", 526.40, "526.40"), p0=_fact("p0", 455.99, "455.99"), tod=_fact("tod", 5632, "5632"))
    tod = evaluate_relation(
        Relation(description="tod", operation="difference", target="tod", operands=["p1", "p0"], factor=80), facts, []
    )
    assert tod.status == "rounding" and tod.difference == pytest.approx(-0.8)
    total = evaluate_relation(Relation(description="sum", operation="sum", target="t", operands=["a", "b"], factor=None), facts, [])
    assert total.status == "pass"
    wrong = evaluate_relation(Relation(description="sum", operation="sum", target="t", operands=["a", "-b"], factor=None), facts, [])
    assert wrong.status == "fail"
    missing = evaluate_relation(Relation(description="x", operation="sum", target="zz", operands=["a"], factor=None), facts, [])
    assert missing.status == "skipped"


def test_misdeclared_meter_product_and_round_sum_amounts():
    facts = {
        "present": _fact("present", 4132.27, "4,132.27"),
        "previous": _fact("previous", 3539.68, "3,539.68"),
        "mf": _fact("mf", 80, "80"),
        "units": _fact("units", 47407, "47407"),
        "wrong": _fact("wrong", 50000, "50000"),
        "total": _fact("total", 631515.48, "631,515.48"),
        "disc": _fact("disc", -5089.97, "5,089.97"),
        "round": _fact("round", 626420.0, "626420.00"),
        "off": _fact("off", 626400.0, "626400.00"),
    }
    # present x previous x mf is meaningless; it is checked as (present - previous) x mf, in any operand order
    for operands in (["present", "previous", "mf"], ["mf", "previous", "present"]):
        meter = evaluate_relation(Relation(description="m", operation="product", target="units", operands=operands, factor=None), facts, [])
        assert meter.status == "pass" and meter.expected == pytest.approx(47407.2) and "present - previous" in meter.details["note"]
    # a reading that fits neither way still fails, with the declared expression
    bad = evaluate_relation(Relation(description="m", operation="product", target="wrong", operands=["present", "previous", "mf"], factor=None), facts, [])
    assert bad.status == "fail" and "note" not in bad.details
    # a payable amount rounded to the nearest 10 is rounding, not a failure; a 25-rupee gap still fails
    rounded = evaluate_relation(Relation(description="r", operation="sum", target="round", operands=["total", "disc"], factor=None), facts, [])
    assert rounded.status == "rounding" and rounded.difference == pytest.approx(-5.51)
    off = evaluate_relation(Relation(description="r", operation="sum", target="off", operands=["total", "disc"], factor=None), facts, [])
    assert off.status == "fail"


def test_relation_over_cell_ranges_across_sheets():
    cells = parse_xlsx(WORKBOOK)
    q1 = evaluate_relation(
        Relation(description="Q1 total vs Jan-Mar", operation="sum", target="Quarterly Summary!G2",
                 operands=["'Monthly Summary 2026'!F3:F5"], factor=None),
        {},
        cells,
    )
    assert (q1.status, q1.expected, q1.actual) == ("fail", 584323, 585323)


def test_classify():
    assert classify(26401.375, 26401.38, 0.01, 0.005)[0] == "pass"
    assert classify(5632.8, 5632, 1, 0.8)[0] == "rounding"
    assert classify(1534290, 1533290, 1, 6)[0] == "fail"


@pytest.mark.parametrize(
    "expr,value",
    [("(14821 - 12500)", 2321), ("1.0 + 0.5 + 0.5 + 0.4", 2.4), ("(4132.27-3539.68) x 80", 47407.2),
     ("1,000 * 2", 2000), ("2 ** 10", None), ("__import__('os')", None), ("1/0", None)],
)
def test_safe_eval(expr, value):
    got = safe_eval(expr)
    assert got == (pytest.approx(value) if value is not None else None)


def test_gemini_schema_keeps_fields_named_like_keywords():
    import json

    from app.llm.gemini import json_schema_for
    from app.schemas import Answer, Extraction

    for model in (Extraction, Answer):
        schema = json_schema_for(model)
        text = json.dumps(schema)
        assert "$ref" not in text and "$defs" not in text
        assert set(schema["required"]) <= set(schema["properties"])
    ex = json_schema_for(Extraction)
    assert "title" in ex["properties"] and "title" not in ex
    fact = ex["properties"]["facts"]["items"]
    assert set(fact["required"]) <= set(fact["properties"])


def test_two_column_quote_verifies_in_reading_order():
    policy = DATASET / "quality_policy_nestle.pdf"
    pages = {p.page_no: p.verify_text for p in parse_pdf(policy)}
    quote = "We never compromise on food safety and \ncompliance. We adhere to internationally"
    assert verify_pdf(pages, 3, quote, None).status == "verified"
    sorted_only = {p.page_no: p.text for p in parse_pdf(policy)}
    assert verify_pdf(sorted_only, 3, quote, None).status != "verified"  # the bug this guards against


def test_old_database_is_migrated(tmp_path):
    import sqlite3

    from app.db import init_db

    db = tmp_path / "old.db"
    old = sqlite3.connect(db)
    old.executescript(
        "CREATE TABLE pages (document_id TEXT, page_no INTEGER, text TEXT NOT NULL, word_count INTEGER NOT NULL,"
        " width REAL, height REAL, PRIMARY KEY (document_id, page_no));"
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, document_id TEXT, status TEXT, attempts INTEGER, max_attempts INTEGER,"
        " run_after REAL, lease_until REAL, worker_id TEXT, last_error TEXT, created_at REAL, updated_at REAL);"
    )
    old.close()
    init_db(db)
    init_db(db)  # idempotent
    conn = sqlite3.connect(db)
    assert "reading_text" in {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
    assert "deferrals" in {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
