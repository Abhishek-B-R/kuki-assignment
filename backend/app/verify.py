"""Check that each extracted fact (or answer citation) really appears where it claims to.

verified   the quote is on the cited page / the cited cell holds the value
partial    the numbers are on the page but the quote text is not verbatim (layout split it), or the
           value was found elsewhere in the document and the location was corrected
unverified nothing matching was found: treat the fact as unsupported
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .numbers import matches_value, numbers_in, parse_number
from .parsing.sheets import Cell, cells_in_range, split_ref

_WORD = re.compile(r"[a-z]{3,}")


def squash(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


@dataclass
class Evidence:
    status: str
    page: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    note: str | None = None


def _quote_on_page(quote: str, page_text: str) -> str | None:
    q = squash(quote)
    if len(q) >= 2 and q in squash(page_text):
        return "verified"
    nums = [n for n in numbers_in(quote)]
    page_nums = numbers_in(page_text)
    words = set(_WORD.findall(quote.lower()))
    page_words = set(_WORD.findall(page_text.lower()))
    if nums and all(matches_value(n, page_nums) or matches_value(abs(n), page_nums) for n in nums):
        if not words or len(words & page_words) / len(words) >= 0.6:
            return "partial"
    return None


def verify_pdf(
    pages: dict[int, str], page: int | None, quote: str | None, value_num: float | None
) -> Evidence:
    """`pages` maps page number to its text layer."""
    if not quote and value_num is None:
        return Evidence("unverified", page, note="no quote or value to check")
    needle = quote or ""
    if needle and value_num is not None and numbers_in(needle):
        # The number the model reports must be one the quote shows (guards against mixing up
        # neighbouring amounts while quoting the right line).
        shown = numbers_in(needle)
        if not (matches_value(value_num, shown) or matches_value(abs(value_num), shown)):
            return Evidence("unverified", page, note="value does not match the quoted text")
    order = ([page] if page in pages else []) + [p for p in sorted(pages) if p != page]
    for p in order:
        status = _quote_on_page(needle, pages[p]) if needle else None
        if status is None and not needle and value_num is not None:
            status = "partial" if matches_value(value_num, numbers_in(pages[p])) else None
        if status:
            if p != page:
                return Evidence("partial", p, note=f"found on page {p}, not page {page}")
            return Evidence(status, p)
    return Evidence("unverified", page, note="quote not found in the document text")


def verify_sheet(
    cells: list[Cell], sheet: str | None, cell_range: str | None, quote: str | None, value_num: float | None
) -> Evidence:
    sheets = {c.sheet for c in cells}
    if not cell_range:
        return Evidence("unverified", sheet=sheet, note="no cell reference")
    parsed = split_ref(cell_range, sheet)
    if parsed is None or parsed[0] not in sheets:
        return Evidence("unverified", sheet=sheet, cell_range=cell_range, note="unknown sheet or range")
    sheet_name, rng = parsed
    in_range = cells_in_range(cells, sheet_name, rng)
    if not in_range:
        return Evidence("unverified", sheet=sheet_name, cell_range=rng, note="range is empty")
    if value_num is not None:
        nums = [c.number for c in in_range if c.number is not None]
        if matches_value(value_num, nums):
            return Evidence("verified", sheet=sheet_name, cell_range=rng)
        return Evidence("unverified", sheet=sheet_name, cell_range=rng, note="no cell in the range holds this value")
    q = squash(quote or "")
    if q and any(q in squash(c.value) or squash(c.value) in q for c in in_range if c.value):
        return Evidence("verified", sheet=sheet_name, cell_range=rng)
    return Evidence("unverified", sheet=sheet_name, cell_range=rng, note="cell text does not match")


def value_from_quote(quote: str | None) -> float | None:
    return parse_number(quote) if quote else None
