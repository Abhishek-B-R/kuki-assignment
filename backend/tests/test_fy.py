from __future__ import annotations

import calendar
import itertools

import pytest

from app.fy import FinancialYear, available_years, fy_report, parse_fy

_ids = itertools.count(1)


def fact(value, start, end, *, metric="electricity_consumption", dim="total", unit="kWh", org="Acme",
         filename="a.pdf", primary=1, label="Units", evidence="verified"):
    return {
        "id": f"f{next(_ids)}", "document_id": filename, "filename": filename, "organisation": org,
        "metric": metric, "dimension": dim, "unit": unit, "value_num": value, "period_start": start,
        "period_end": end, "is_primary": primary, "label": label, "page": 1, "sheet": None, "cell_range": None,
        "quote": str(value), "evidence_status": evidence,
    }


def months(year, values, start_month=1, **kw):
    out = []
    for i, v in enumerate(values):
        m = start_month + i
        y = year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        last = calendar.monthrange(y, m)[1]
        out.append(fact(v, f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last}", label=calendar.month_name[m], **kw))
    return out


@pytest.mark.parametrize(
    "text,start",
    [("FY 2024-25", 2024), ("fy24-25", 2024), ("Financial Year 2025-2026", 2025), ("FY25", 2024),
     ("FY 2026", 2025), ("2023-24", 2023), ("F.Y. 2022-23", 2022)],
)
def test_parse_fy(text, start):
    assert parse_fy(text).start_year == start


def test_parse_fy_rejects_non_years():
    assert parse_fy("billing period 01-06-2024 - 01-07-2024") is None
    assert parse_fy("2024-2026") is None
    assert parse_fy("what is the total") is None


def test_single_year_form_states_assumption():
    rep = fy_report([], parse_fy("FY25"))
    assert rep["financial_year"] == "FY 2024-25"
    assert "ending 31 March 2025" in rep["notes"][0]


def test_calendar_workbook_split_across_financial_years():
    facts = months(2026, [100] * 12, filename="wb.xlsx")
    rep = fy_report(facts, FinancialYear(2025))
    (g,) = rep["groups"]
    assert g["total"] == 300 and g["coverage"]["months_covered"] == 3
    assert g["coverage"]["months_missing"][:2] == ["2025-04", "2025-05"]
    assert not g["coverage"]["complete"]
    rep = fy_report(facts, FinancialYear(2026))
    assert rep["groups"][0]["total"] == 900
    assert "Coverage is incomplete" in " ".join(rep["notes"])


def test_aggregate_is_not_double_counted_and_mismatch_is_reported():
    facts = months(2026, [10, 20, 30], filename="wb.xlsx")
    facts.append(fact(61, "2026-01-01", "2026-03-31", label="Q1", filename="wb.xlsx"))  # off by one
    facts.append(fact(500, "2026-01-01", "2026-12-31", label="Annual", filename="wb.xlsx"))
    (g,) = fy_report(facts, FinancialYear(2025))["groups"]
    assert g["total"] == 60
    assert [r["label"] for r in g["records"]] == ["January", "February", "March"]
    (cc,) = g["cross_checks"]
    assert cc["status"] == "fail" and cc["difference"] == 1
    excluded = fy_report(facts, FinancialYear(2025))["excluded"]
    assert {e["label"] for e in excluded} == {"Q1", "Annual"}


def test_boundary_period_is_prorated():
    f = fact(310, "2025-03-15", "2025-04-14")  # 31 days, 14 of them in FY 2025-26
    (g,) = fy_report([f], FinancialYear(2025))["groups"]
    assert g["includes_prorated"]
    assert g["total"] == pytest.approx(140)
    assert g["records"][0]["share"] == pytest.approx(14 / 31, abs=1e-4)


def test_organisations_metrics_and_units_are_never_mixed():
    facts = [
        fact(47407, "2024-04-01", "2024-04-30", org="J Kumar"),
        fact(63000, "2024-06-01", "2024-07-01", org="Prestige"),
        fact(2321, "2024-12-01", "2024-12-31", org="Prestige", metric="water_consumption", unit="Cubic Meters"),
        fact(1.5, "2024-05-01", "2024-05-31", org="J Kumar", unit="MWh"),
    ]
    rep = fy_report(facts, FinancialYear(2024))
    by = {(g["organisation"], g["metric"]): g for g in rep["groups"]}
    assert by[("J Kumar", "electricity_consumption")]["total"] == 47407 + 1500  # MWh converted
    assert by[("Prestige", "electricity_consumption")]["total"] == 63000
    assert by[("Prestige", "water_consumption")]["unit"] == "m3"
    assert "2 different organisations" in " ".join(rep["notes"])
    june = by[("Prestige", "electricity_consumption")]["coverage"]
    assert june["months_covered"] == 1 and june["months_partial"] == ["2024-07"]


def test_secondary_and_out_of_year_values_are_excluded():
    facts = [
        fact(43037, "2024-03-01", "2024-03-31", primary=0, label="Previous units"),
        fact(47407, "2024-04-01", "2024-04-30"),
        fact(1.0, "2023-03-10", "2023-03-10", metric="waste_quantity", unit="T", filename="waste.pdf"),
    ]
    rep = fy_report(facts, FinancialYear(2024))
    assert [g["total"] for g in rep["groups"]] == [47407]
    assert rep["documents_outside_fy"] == {"a.pdf": ["FY 2023-24"], "waste.pdf": ["FY 2022-23"]}
    rep = fy_report(facts, FinancialYear(2023))
    assert rep["excluded"][0]["reason"].startswith("secondary")
    assert rep["groups"] == []


def test_available_years():
    facts = months(2026, [1] * 12) + [fact(5, "2024-06-01", "2024-07-01")]
    assert available_years(facts) == ["FY 2024-25", "FY 2025-26", "FY 2026-27"]


def test_rates_shares_capacities_and_statistics_are_never_totalled():
    fy = parse_fy("FY 2025-26")
    def labelled(rows, label):
        return [{**r, "label": label} for r in rows]

    facts = (
        labelled(months(2026, [66, 65.2, 63.7], dim="grid", unit="%"), "Grid %")
        + labelled(months(2026, [5.94, 5.86, 5.73], metric="cost", unit="INR/kWh"), "Cost per kWh")
        + [fact(250, "2026-01-01", "2026-12-31", metric="renewable_generation", dim="solar", unit="kWp", label="Solar Capacity"),
           fact(214900, "2026-01-01", "2026-12-31", label="Highest Total Consumption Value"),
           fact(5000, "2026-01-01", "2026-01-31", metric="cost", unit="INR", label="Maximum demand charges")]
        + months(2026, [100, 200, 300])
    )
    rep = fy_report(facts, fy)
    groups = {(g["metric"], g["dimension"], g["unit"]): g for g in rep["groups"]}
    assert set(groups) == {("electricity_consumption", "total", "kWh"), ("cost", "total", "INR")}
    assert groups[("electricity_consumption", "total", "kWh")]["total"] == 600  # the "highest" statistic is not counted
    assert groups[("cost", "total", "INR")]["total"] == 5000  # a money line named after a statistic still counts
    reasons = {e["label"]: e["reason"] for e in rep["excluded"]}
    assert "not totalled" in reasons["Grid %"] and "not totalled" in reasons["Cost per kWh"]
    assert "not totalled" in reasons["Solar Capacity"] and "statistic" in reasons["Highest Total Consumption Value"]


def test_lakh_and_crore_amounts_are_converted_to_inr():
    fy = parse_fy("FY 2025-26")
    rep = fy_report([fact(2.5, "2025-04-01", "2025-04-30", metric="cost", unit="lakhs INR"),
                     fact(0.1, "2025-05-01", "2025-05-31", metric="cost", unit="crore")], fy)
    (g,) = rep["groups"]
    assert g["unit"] == "INR" and g["total"] == 1_250_000


def test_unit_spellings_normalise():
    from app.numbers import normalise_unit

    assert normalise_unit("cubic_meters") == ("m3", 1.0)
    assert normalise_unit("INR Lakhs") == ("INR", 100_000.0)
    assert normalise_unit("Crores") == ("INR", 10_000_000.0)
