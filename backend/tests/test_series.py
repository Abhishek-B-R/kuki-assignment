from __future__ import annotations

from datetime import date

import pytest

from app.fy import FinancialYear, fy_report
from app.parsing.sheets import parse_xlsx
from app.schemas import SheetSeries
from app.series import expand_series, parse_period

from .conftest import DATASET

CELLS = parse_xlsx(DATASET / "Annual Electricity Consumption.xlsx")


@pytest.mark.parametrize(
    "label,year,basis,expected",
    [
        ("January", 2026, "calendar", (date(2026, 1, 1), date(2026, 1, 31))),
        ("Feb", 2024, "calendar", (date(2024, 2, 1), date(2024, 2, 29))),
        ("Jan - Mar", 2026, "calendar", (date(2026, 1, 1), date(2026, 3, 31))),
        ("Oct - Dec", 2026, "calendar", (date(2026, 10, 1), date(2026, 12, 31))),
        ("Nov - Jan", 2026, "calendar", (date(2026, 11, 1), date(2027, 1, 31))),
        ("Q2", 2026, "calendar", (date(2026, 4, 1), date(2026, 6, 30))),
        ("Q1", 2025, "indian_fy", (date(2025, 4, 1), date(2025, 6, 30))),
        ("Q4", 2025, "indian_fy", (date(2026, 1, 1), date(2026, 3, 31))),
        ("Mar 2025", 2026, "calendar", (date(2025, 3, 1), date(2025, 3, 31))),
        ("2026-03-01T00:00:00", 2020, "calendar", (date(2026, 3, 1), date(2026, 3, 31))),
        ("ANNUAL TOTAL", 2026, "calendar", None),
    ],
)
def test_parse_period(label, year, basis, expected):
    assert parse_period(label, year, basis) == expected


def series(header, col, metric="electricity_consumption", dim="total", sheet="Monthly Summary 2026",
           first=3, last=14, label_col="A"):
    return SheetSeries(key=header.lower().replace(" ", "_"), header=header, sheet=sheet, label_column=label_col,
                       value_column=col, first_row=first, last_row=last, metric=metric, dimension=dim, unit="kWh",
                       year=2026, quarter_basis="calendar", year_note="sheet title")


def test_expand_reads_every_month_from_cells():
    facts, problems = expand_series([series("Total Consumption (kWh)", "F")], CELLS)
    assert problems == []
    assert len(facts) == 12
    jan = facts[0]
    assert (jan.value_num, jan.cell_range, jan.period_start, jan.period_end) == (200823, "F3", "2026-01-01", "2026-01-31")
    assert facts[-1].period_start == "2026-12-01" and facts[-1].cell_range == "F14"
    assert all(f.metric == "electricity_consumption" and f.primary for f in facts)


def test_year_wraps_for_fy_ordered_labels(tmp_path):
    from app.parsing.sheets import parse_csv

    p = tmp_path / "fy.csv"
    p.write_text("Month,kWh\n" + "\n".join(f"{m},{i}" for i, m in enumerate(
        ["April", "May", "June", "July", "August", "September", "October", "November", "December", "January", "February", "March"], 1)))
    cells = parse_csv(p)
    s = series("kWh", "B", sheet="fy", first=2, last=13)
    s.year = 2025
    facts, _ = expand_series([s], cells)
    assert facts[0].period_start == "2025-04-01" and facts[-1].period_start == "2026-03-01"
    (g,) = fy_report([_row(f) for f in facts], FinancialYear(2025))["groups"]
    assert g["coverage"]["complete"] and g["total"] == sum(range(1, 13))


def test_unreadable_labels_are_reported_not_guessed():
    s = series("Total Consumption (kWh)", "F", first=3, last=16)  # includes the blank row and ANNUAL TOTAL
    facts, problems = expand_series([s], CELLS)
    assert len(facts) == 12
    assert problems == ["Series 'Total Consumption (kWh)': could not read a period from Monthly Summary 2026!A16 ('ANNUAL TOTAL')"]


def _row(f, filename="wb.xlsx"):
    return {**f.model_dump(), "id": f.id, "document_id": filename, "filename": filename, "organisation": None,
            "is_primary": 1, "evidence_status": "verified"}


def test_v2_series_fix_fy_totals_and_expose_quarterly_conflict():
    monthly, _ = expand_series([series("Total Consumption (kWh)", "F")], CELLS)
    quarterly, _ = expand_series(
        [series("Total (kWh)", "G", sheet="Quarterly Summary", first=2, last=5, label_col="B")], CELLS
    )
    facts = [_row(f) for f in monthly + quarterly]
    (g,) = fy_report(facts, FinancialYear(2025))["groups"]
    assert g["total"] == 584323 and not g["includes_prorated"]
    assert g["coverage"]["months_covered"] == 3
    (cc,) = g["cross_checks"]
    assert (cc["stated"], cc["sum_of_parts"], cc["status"]) == (585323, 584323, "fail")
    (g,) = fy_report(facts, FinancialYear(2026))["groups"]
    assert g["total"] == 1841800 and g["coverage"]["months_missing"] == ["2027-01", "2027-02", "2027-03"]


def test_null_quarter_basis_is_accepted_and_read_as_calendar():
    # Models send null for columns whose labels are months; that must not reject the whole extraction.
    raw = series("Total Consumption (kWh)", "F").model_dump() | {"quarter_basis": None}
    s = SheetSeries.model_validate(raw)
    facts, problems = expand_series([s], CELLS)
    assert problems == [] and len(facts) == 12 and facts[0].period_start == "2026-01-01"
    assert parse_period("Q1", 2026, s.quarter_basis or "calendar") == (date(2026, 1, 1), date(2026, 3, 31))
