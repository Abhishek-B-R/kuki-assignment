"""Indian financial year (1 April - 31 March) selection, totals and coverage.

All arithmetic is done here, not by the model. Rules:
- Only facts with a metric, a parseable period and primary=True count. Secondary values
  (history charts, previous-month figures, averages) are listed as excluded.
- Values are grouped by organisation, metric, dimension and unit. Different groups are
  never added together.
- Within a group the finest-grained records win: an annual or quarterly total that overlaps
  monthly records is excluded (no double counting), but compared against the sum of the
  records it covers so disagreements surface.
- A record that straddles the FY boundary is prorated by days, and flagged as an estimate.
- Rates, shares, ratios, capacities and statistics (%, INR/kWh, kWp, "average", "highest") are
  not quantities over time: adding or prorating them is meaningless, so they are excluded.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .numbers import fmt, normalise_unit

# Units that are not amounts accumulated over a period: shares, ratios, per-unit rates, power/capacity.
NON_ADDITIVE_UNIT = re.compile(r"%|/|\bper\b|\bratio\b|^(k|m|g)?(w|wp|va)$|^hp$", re.I)
# Statistics about a period rather than amounts in it. Not applied to money: bill lines such as
# "Maximum demand charges" are real amounts even though the label names a statistic.
STATISTIC_LABEL = re.compile(
    r"%|\b(average|avg|mean|highest|lowest|max(imum)?|min(imum)?|capacity|ratio|utili[sz]ation|percent(age)?)\b", re.I
)


def non_additive_reason(label: str | None, unit: str | None) -> str | None:
    if unit and NON_ADDITIVE_UNIT.search(unit.strip()):
        return f"a rate, share or capacity ({unit.strip()}), not an amount over time; not totalled"
    if label and unit != "INR" and STATISTIC_LABEL.search(label):
        return "a statistic about the period, not an amount in it; not totalled"
    return None

TOTALABLE = {
    "electricity_consumption",
    "renewable_generation",
    "water_consumption",
    "waste_quantity",
    "amount_payable",
    "cost",
    "savings",
    "emissions_avoided",
}


@dataclass(frozen=True)
class FinancialYear:
    start_year: int
    assumption: str | None = None

    @property
    def label(self) -> str:
        return f"FY {self.start_year}-{str(self.start_year + 1)[-2:]}"

    @property
    def start(self) -> date:
        return date(self.start_year, 4, 1)

    @property
    def end(self) -> date:
        return date(self.start_year + 1, 3, 31)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def _year(s: str, base: int | None = None) -> int:
    y = int(s)
    if y < 100:
        century = (base // 100 * 100) if base else 2000
        y += century
        if base and y < base:
            y += 100
    return y


def parse_fy(text: str) -> FinancialYear | None:
    """Understands 'FY 2024-25', 'FY24-25', 'FY 2024/2025', '2024-25', 'financial year 2024-2025',
    'FY25' and 'FY2025' (single year = the year the FY ends, the usual Indian convention)."""
    t = text.replace("–", "-").replace("—", "-")
    pair = r"\b(?:F\.?\s?Y\.?|financial\s+year|fiscal\s+year)?\s*'?((?:19|20)?\d{2})\s*[-/]\s*'?((?:19|20)?\d{2})\b(?![-/]\d)"
    for m in re.finditer(pair, t, re.I):
        a = _year(m.group(1))
        b = _year(m.group(2), a)
        if b == a + 1 and (len(m.group(1)) == 4 or re.search(r"F\.?\s?Y|financial|fiscal", m.group(0), re.I)):
            return FinancialYear(a)
    m = re.search(r"\b(?:F\.?\s?Y\.?|financial\s+year|fiscal\s+year)\s*'?((?:19|20)?\d{2})\b(?!\s*[-/])", t, re.I)
    if m:
        end = _year(m.group(1))
        return FinancialYear(end - 1, f"Read '{m.group(0).strip()}' as the year ending 31 March {end} (FY {end - 1}-{str(end)[-2:]}).")
    return None


def fy_of(d: date) -> FinancialYear:
    return FinancialYear(d.year if d.month >= 4 else d.year - 1)


def _date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _norm_org(org: str | None, filename: str) -> str:
    return org.strip() if org and org.strip() else f"Unknown organisation ({filename})"


def _overlap(a0: date, a1: date, b0: date, b1: date) -> int:
    return max(0, (min(a1, b1) - max(a0, b0)).days + 1)


def _month_iter(fy: FinancialYear):
    y, m = fy.start.year, 4
    for _ in range(12):
        last = calendar.monthrange(y, m)[1]
        yield date(y, m, 1), date(y, m, last)
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _ref(f: dict) -> dict:
    return {
        "fact_id": f["id"],
        "document_id": f["document_id"],
        "filename": f["filename"],
        "page": f.get("page"),
        "sheet": f.get("sheet"),
        "cell_range": f.get("cell_range"),
        "quote": f.get("quote"),
        "evidence_status": f.get("evidence_status"),
    }


def fy_report(facts: list[dict], fy: FinancialYear, metrics: set[str] | None = None) -> dict[str, Any]:
    """`facts` rows joined with their document's filename/organisation/doc_type."""
    groups: dict[tuple, list[dict]] = {}
    excluded: list[dict] = []
    outside: dict[str, set[str]] = {}

    for f in facts:
        metric = f.get("metric")
        if metric not in TOTALABLE or (metrics and metric not in metrics):
            continue
        p0, p1 = _date(f.get("period_start")), _date(f.get("period_end"))
        if p0 is None or f.get("value_num") is None:
            continue
        p1 = p1 or p0
        if p1 < p0:
            p0, p1 = p1, p0
        days = (p1 - p0).days + 1
        ov = _overlap(p0, p1, fy.start, fy.end)
        if ov == 0:
            outside.setdefault(f["filename"], set()).add(fy_of(p0).label)
            continue
        unit, factor = normalise_unit(f.get("unit"))
        rec = {
            **f,
            "_p0": p0,
            "_p1": p1,
            "_days": days,
            "_overlap": ov,
            "_value": f["value_num"] * factor,
            "_unit": unit,
        }
        if reason := non_additive_reason(f.get("label"), unit):
            excluded.append({**_brief(rec), "reason": reason})
            continue
        if not f.get("is_primary", 1):
            excluded.append({**_brief(rec), "reason": "secondary value (history, previous period or average)"})
            continue
        key = (_norm_org(f.get("organisation"), f["filename"]), metric, (f.get("dimension") or "total").strip().lower(), unit)
        groups.setdefault(key, []).append(rec)

    out_groups = []
    for (org, metric, dim, unit), recs in sorted(groups.items(), key=lambda kv: kv[0]):
        recs.sort(key=lambda r: (r["_days"], r["_p0"]))
        selected: list[dict] = []
        dropped: list[dict] = []
        for r in recs:
            if any(_overlap(r["_p0"], r["_p1"], s["_p0"], s["_p1"]) for s in selected):
                dropped.append(r)
            else:
                selected.append(r)
        selected.sort(key=lambda r: r["_p0"])

        records = []
        total = 0.0
        prorated = False
        for r in selected:
            share = r["_overlap"] / r["_days"]
            contribution = r["_value"] * share
            prorated |= share < 1
            total += contribution
            records.append(
                {
                    **_brief(r),
                    "included_days": r["_overlap"],
                    "share": round(share, 4),
                    "contribution": round(contribution, 4),
                    "prorated": share < 1,
                }
            )

        cross_checks = []
        for d in dropped:
            inner = [s for s in selected if s["_p0"] >= d["_p0"] and s["_p1"] <= d["_p1"]]
            covered = sum(s["_days"] for s in inner)
            entry = {**_brief(d), "reason": "overlaps finer-grained records already counted"}
            if inner and covered >= d["_days"] * 0.97:
                inner_sum = sum(s["_value"] for s in inner)
                diff = d["_value"] - inner_sum
                agrees = abs(diff) <= max(0.5, abs(inner_sum) * 1e-9)
                cross_checks.append(
                    {
                        "description": f"{d['label']} ({d['period_start']} to {d['period_end']}) vs sum of {len(inner)} finer records",
                        "stated": d["_value"],
                        "sum_of_parts": inner_sum,
                        "difference": diff,
                        "status": "pass" if agrees else "fail",
                        "stated_ref": _ref(d),
                    }
                )
                if not agrees:
                    entry["reason"] += f"; it disagrees with their sum by {fmt(diff, unit)}"
            elif any(s["_p0"] == d["_p0"] and s["_p1"] == d["_p1"] for s in selected):
                entry["reason"] = "same period already counted from another record (possible duplicate)"
            excluded.append(entry)

        covered_days = sum(r["_overlap"] for r in selected)
        months = []
        for m0, m1 in _month_iter(fy):
            mdays = (m1 - m0).days + 1
            c = sum(_overlap(r["_p0"], r["_p1"], m0, m1) for r in selected)
            months.append({"month": m0.strftime("%Y-%m"), "covered_days": min(c, mdays), "days": mdays,
                           "status": "covered" if c >= mdays * 0.9 else ("partial" if c > 0 else "missing")})
        n_cov = sum(m["status"] == "covered" for m in months)
        expr = " + ".join(
            fmt(rec["contribution"]) + (f" ({rec['share']:.0%} of {fmt(rec['value'])})" if rec["prorated"] else "") for rec in records
        )
        out_groups.append(
            {
                "organisation": org,
                "metric": metric,
                "dimension": dim,
                "unit": unit,
                "total": round(total, 4),
                "origin": "calculated",
                "calculation": f"{expr} = {fmt(round(total, 4), unit)}",
                "records": records,
                "coverage": {
                    "covered_days": covered_days,
                    "fy_days": fy.days,
                    "ratio": round(covered_days / fy.days, 4),
                    "months_covered": n_cov,
                    "months_missing": [m["month"] for m in months if m["status"] == "missing"],
                    "months_partial": [m["month"] for m in months if m["status"] == "partial"],
                    "months": months,
                    "complete": n_cov == 12,
                },
                "includes_prorated": prorated,
                "cross_checks": cross_checks,
                "unverified_records": sum(1 for r in selected if r.get("evidence_status") == "unverified"),
            }
        )

    orgs = sorted({g["organisation"] for g in out_groups})
    notes = []
    if fy.assumption:
        notes.append(fy.assumption)
    if len(orgs) > 1:
        notes.append(f"Records belong to {len(orgs)} different organisations; totals are kept separate per organisation.")
    if any(g["includes_prorated"] for g in out_groups):
        notes.append("Some periods straddle the financial-year boundary; their values were prorated by days, assuming even consumption.")
    if any(not g["coverage"]["complete"] for g in out_groups):
        notes.append("Coverage is incomplete: at least one series is missing months in this financial year.")
    if any(g["unverified_records"] for g in out_groups):
        notes.append("Some included records could not be verified against the document text.")
    if any(c["status"] == "fail" for g in out_groups for c in g["cross_checks"]):
        notes.append("A stated aggregate disagrees with the sum of its monthly records; monthly records were used.")

    return {
        "financial_year": fy.label,
        "start": fy.start.isoformat(),
        "end": fy.end.isoformat(),
        "groups": out_groups,
        "excluded": excluded,
        "documents_outside_fy": {k: sorted(v) for k, v in sorted(outside.items())},
        "organisations": orgs,
        "notes": notes,
    }


def _brief(r: dict) -> dict:
    return {
        "label": r.get("label"),
        "value": r["_value"],
        "unit": r["_unit"],
        "period_start": r["_p0"].isoformat(),
        "period_end": r["_p1"].isoformat(),
        "dimension": r.get("dimension"),
        "source": _ref(r),
    }


def available_years(facts: list[dict]) -> list[str]:
    years: set[int] = set()
    for f in facts:
        if f.get("metric") not in TOTALABLE:
            continue
        p0, p1 = _date(f.get("period_start")), _date(f.get("period_end"))
        if p0 is None:
            continue
        p1 = p1 or p0
        d = p0
        while d <= p1:
            years.add(fy_of(d).start_year)
            d = date(d.year + 1, 4, 1) if d.month >= 4 else date(d.year, 4, 1)
    return [FinancialYear(y).label for y in sorted(years)]
