"""Expand declared spreadsheet series into one cell-cited fact per row (extraction v2).

The model only says which columns hold which metric and where the period labels are; every
value and period is then read from the cells by code, so nothing is retyped by the model."""

from __future__ import annotations

import calendar
import re
from datetime import date

from openpyxl.utils import column_index_from_string

from .parsing.sheets import Cell
from .schemas import ExtractedFact, SheetSeries

_MONTHS = {name.lower(): i for i, name in enumerate(calendar.month_name) if name}
_MONTHS.update({name.lower(): i for i, name in enumerate(calendar.month_abbr) if name})
_MONTHS["sept"] = 9

CATEGORY = {
    "electricity_consumption": "consumption",
    "water_consumption": "consumption",
    "waste_quantity": "quantity",
    "renewable_generation": "generation",
    "amount_payable": "financial",
    "cost": "financial",
    "savings": "financial",
    "emissions_avoided": "quantity",
}


def _month(token: str) -> int | None:
    return _MONTHS.get(token.strip().lower().rstrip("."))


def _end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def parse_period(label: str, year: int, quarter_basis: str = "calendar") -> tuple[date, date] | None:
    """'January' -> Jan of `year`; 'Jan - Mar' -> Q range; 'Q2' -> quarter; '2026-03-01' -> that month;
    'Mar 2026' / 'March-26' -> month with its own year."""
    s = label.strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})-\d{2}(?:[T ].*)?", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return date(y, mo, 1), _end(y, mo)
    m = re.fullmatch(r"([A-Za-z]+)\.?\s*[-/' ]?\s*'?(\d{2}|\d{4})", s)
    if m and _month(m.group(1)):
        y = int(m.group(2))
        y = y + 2000 if y < 100 else y
        mo = _month(m.group(1))
        return date(y, mo, 1), _end(y, mo)
    if _month(s):
        mo = _month(s)
        return date(year, mo, 1), _end(year, mo)
    m = re.fullmatch(r"([A-Za-z]+)\.?\s*(?:-|–|to)\s*([A-Za-z]+)\.?", s)
    if m and _month(m.group(1)) and _month(m.group(2)):
        a, b = _month(m.group(1)), _month(m.group(2))
        yb = year if b >= a else year + 1
        return date(year, a, 1), _end(yb, b)
    m = re.fullmatch(r"Q([1-4])", s, re.I)
    if m:
        q = int(m.group(1))
        start = (q - 1) * 3 + 1 if quarter_basis == "calendar" else (q * 3 + 1 if q < 4 else 1)
        y = year if not (quarter_basis == "indian_fy" and q == 4) else year + 1
        return date(y, start, 1), _end(y, start + 2)
    return None


def expand_series(series: list[SheetSeries], cells: list[Cell]) -> tuple[list[ExtractedFact], list[str]]:
    """Returns facts and human-readable notes about rows that could not be read."""
    index = {(c.sheet, c.row, c.col): c for c in cells}
    facts: list[ExtractedFact] = []
    notes: list[str] = []
    for si, s in enumerate(series, start=1):
        try:
            lcol, vcol = column_index_from_string(s.label_column.upper()), column_index_from_string(s.value_column.upper())
        except ValueError:
            notes.append(f"Series '{s.header}': invalid column {s.label_column!r}/{s.value_column!r}")
            continue
        year, prev_month = s.year, None
        for row in range(s.first_row, s.last_row + 1):
            label = index.get((s.sheet, row, lcol))
            value = index.get((s.sheet, row, vcol))
            if label is None or value is None or value.number is None:
                continue
            period = parse_period(label.value, year, s.quarter_basis)
            if period is None:
                notes.append(f"Series '{s.header}': could not read a period from {s.sheet}!{label.ref} ({label.value!r})")
                continue
            # A label sequence that wraps (Dec -> Jan) moves into the next year.
            if prev_month is not None and period[0].month < prev_month and period[0].year == year:
                year += 1
                period = parse_period(label.value, year, s.quarter_basis) or period
            prev_month = period[0].month
            note = f"period from {s.sheet}!{label.ref} '{label.value}'"
            if s.year_note:
                note += f"; year: {s.year_note}"
            facts.append(
                ExtractedFact(
                    id=f"s{si}r{row}", key=s.key, label=f"{s.header} - {label.value}", category=CATEGORY.get(s.metric, "other"),
                    metric=s.metric, dimension=s.dimension, primary=True, value_text=value.value, value_num=value.number,
                    unit=s.unit, period_start=period[0].isoformat(), period_end=period[1].isoformat(), page=None,
                    sheet=s.sheet, cell_range=value.ref, quote=value.value, notes=note,
                )
            )
    return facts, notes
