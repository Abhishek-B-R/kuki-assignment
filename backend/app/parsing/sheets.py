"""Spreadsheet parsing (xlsx via openpyxl, csv) into addressable cells."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter, range_boundaries

from ..numbers import parse_number


class SheetError(Exception):
    """The file is not a readable spreadsheet."""


@dataclass
class Cell:
    sheet: str
    ref: str
    row: int
    col: int
    value: str
    number: float | None
    formula: str | None


def parse_xlsx(path: Path) -> list[Cell]:
    try:
        wb_values = openpyxl.load_workbook(path, data_only=True, read_only=False)
        wb_formulas = openpyxl.load_workbook(path, data_only=False, read_only=False)
    except Exception as exc:
        raise SheetError(f"Cannot open workbook: {exc}") from exc
    cells: list[Cell] = []
    for ws in wb_values.worksheets:
        wf = wb_formulas[ws.title]
        for row in ws.iter_rows():
            for c in row:
                raw = c.value
                f = wf[c.coordinate].value
                formula = f if isinstance(f, str) and f.startswith("=") else None
                if raw is None and formula is None:
                    continue
                text = "" if raw is None else (raw.isoformat() if hasattr(raw, "isoformat") else str(raw))
                number = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else parse_number(text)
                cells.append(Cell(ws.title, c.coordinate, c.row, c.column, text, number, formula))
    if not cells:
        raise SheetError("Workbook has no cell values")
    return cells


def parse_csv(path: Path) -> list[Cell]:
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise SheetError(f"Cannot read CSV: {exc}") from exc
    sheet = path.stem
    cells = []
    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row, start=1):
            if val.strip() == "":
                continue
            cells.append(Cell(sheet, f"{get_column_letter(c)}{r}", r, c, val, parse_number(val), None))
    if not cells:
        raise SheetError("CSV is empty")
    return cells


def sheet_names(cells: list[Cell]) -> list[str]:
    seen: dict[str, None] = {}
    for c in cells:
        seen.setdefault(c.sheet, None)
    return list(seen)


def render_for_model(cells: list[Cell]) -> str:
    """Row-wise dump with coordinates, e.g. `R3: A3="January" B3=132590`, one block per sheet.
    The model cites cells from this text, and we check its citations against the same cells."""
    out = []
    for name in sheet_names(cells):
        out.append(f"### Sheet: {name}")
        rows: dict[int, list[Cell]] = {}
        for c in cells:
            if c.sheet == name:
                rows.setdefault(c.row, []).append(c)
        for r in sorted(rows):
            parts = []
            for c in sorted(rows[r], key=lambda x: x.col):
                # Plain numbers unquoted; text (including "34.0%") quoted so it reads as written.
                parts.append(f"{c.ref}={c.value}" if _plain_number(c.value) else f"{c.ref}={c.value!r}")
            out.append(f"R{r}: " + "  ".join(parts))
        out.append("")
    return "\n".join(out)


def _plain_number(s: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", s.strip()))


_RANGE = re.compile(r"^(?:(?P<sheet>.+)!)?(?P<range>\$?[A-Z]{1,3}\$?\d+(?::\$?[A-Z]{1,3}\$?\d+)?)$")


def split_ref(ref: str, default_sheet: str | None = None) -> tuple[str | None, str] | None:
    """`'Monthly Summary 2026'!B3:B14` -> ("Monthly Summary 2026", "B3:B14")."""
    ref = ref.strip()
    m = _RANGE.match(ref)
    if not m:
        return None
    sheet = m.group("sheet")
    if sheet is not None:
        sheet = sheet.strip()
        if len(sheet) >= 2 and sheet[0] == sheet[-1] == "'":
            sheet = sheet[1:-1].replace("''", "'")
    return (sheet or default_sheet, m.group("range").replace("$", ""))


def cells_in_range(cells: list[Cell], sheet: str, rng: str) -> list[Cell]:
    try:
        min_col, min_row, max_col, max_row = range_boundaries(rng)
    except ValueError:
        return []
    return [
        c
        for c in cells
        if c.sheet == sheet and min_row <= c.row <= max_row and min_col <= c.col <= max_col
    ]
