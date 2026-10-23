"""Deterministic arithmetic checks. The model may declare which numbers should relate; only this
code does the arithmetic, so a reported total is never confused with one we calculated."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .numbers import fmt
from .parsing.sheets import Cell, cells_in_range, split_ref
from .schemas import ExtractedFact, Relation

NON_ADDITIVE_HEADER = re.compile(
    r"%|\b(per|rates?|ratios?|factors?|average|avg|mean|price|tariff|cuf|utili[sz]ation|percent(age)?)\b", re.I
)
TOTAL_LABEL = re.compile(r"\b(grand\s+)?total\b|\bannual\b|\bsum\b", re.I)


@dataclass
class CheckResult:
    name: str
    status: str  # pass | rounding | fail | skipped
    expected: float | None
    actual: float | None
    difference: float | None
    source: str
    details: dict = field(default_factory=dict)


def decimals(text: str | None) -> int:
    if not text:
        return 0
    m = re.search(r"\d+\.(\d+)", text.replace(",", ""))
    return len(m.group(1)) if m else 0


def classify(expected: float, actual: float, target_step: float, operand_slack: float) -> tuple[str, float]:
    """`expected` is what we calculated, `actual` what the document states.
    pass: equal at the stated precision. rounding: off by no more than the precision of the
    numbers involved. fail: anything larger."""
    diff = actual - expected
    if abs(diff) <= target_step / 2 + 1e-9:
        return "pass", diff
    if abs(diff) <= max(target_step, operand_slack) + 1e-9:
        return "rounding", diff
    return "fail", diff


@dataclass
class _Operand:
    value: float
    step: float
    label: str
    refs: list[str]


def _resolve(token: str, facts: dict[str, ExtractedFact], cells: list[Cell]) -> _Operand | None:
    token = token.strip()
    if token in facts:
        f = facts[token]
        if f.value_num is None:
            return None
        step = 10 ** -decimals(f.value_text)
        loc = f"p{f.page}" if f.page else (f"{f.sheet}!{f.cell_range}" if f.cell_range else "")
        return _Operand(f.value_num, step, f"{f.label} ({f.value_text})", [f"{token} {loc}".strip()])
    parsed = split_ref(token)
    if parsed and parsed[0]:
        sheet, rng = parsed
        nums = [c for c in cells_in_range(cells, sheet, rng) if c.number is not None]
        if not nums:
            return None
        step = max(10 ** -decimals(c.value) for c in nums)
        label = f"{sheet}!{rng}" + (f" (sum of {len(nums)} cells)" if len(nums) > 1 else "")
        return _Operand(sum(c.number for c in nums), step * len(nums), label, [f"{sheet}!{rng}"])
    return None


def evaluate_relation(rel: Relation, facts: dict[str, ExtractedFact], cells: list[Cell]) -> CheckResult:
    target = _resolve(rel.target, facts, cells)
    signed: list[tuple[int, _Operand | None]] = []
    for tok in rel.operands:
        sign = -1 if tok.strip().startswith("-") else 1
        signed.append((sign, _resolve(tok.strip().lstrip("-"), facts, cells)))
    if target is None or not signed or any(op is None for _, op in signed):
        return CheckResult(rel.description, "skipped", None, None, None, "extractor",
                           {"reason": "an operand has no numeric value", "target": rel.target, "operands": rel.operands})
    ops = [(s, op) for s, op in signed if op is not None]
    factor = rel.factor if rel.factor not in (None, 0) else 1.0
    if rel.operation == "sum":
        expected = sum(s * op.value for s, op in ops) * factor
        expr = " + ".join(("-" if s < 0 else "") + fmt(op.value) for s, op in ops)
    elif rel.operation == "difference":
        if len(ops) != 2:
            return CheckResult(rel.description, "skipped", None, None, None, "extractor", {"reason": "difference needs two operands"})
        expected = (ops[0][1].value - ops[1][1].value) * factor
        expr = f"({fmt(ops[0][1].value)} - {fmt(ops[1][1].value)})"
    else:
        expected = factor
        for _, op in ops:
            expected *= op.value
        expr = " x ".join(fmt(op.value) for _, op in ops)
    if factor != 1.0:
        expr = f"{expr} x {fmt(factor)}"
    slack = sum(op.step for _, op in ops) * abs(factor) / 2 if rel.operation != "product" else abs(expected) * 1e-4
    status, diff = classify(expected, target.value, target.step, slack)
    notes: list[str] = []
    if status == "fail" and rel.operation == "product" and len(ops) == 3 and all(s > 0 for s, _ in ops):
        # Models often declare "(present - previous) x multiplying factor" as a plain product of all three.
        # Multiplying two meter readings is meaningless, so try the reading the document must have meant.
        alt = _meter_reading(ops, factor, target)
        if alt:
            expected, expr, status, diff = alt
            notes.append("declared as a product of all three values; checked as (present - previous) x factor")
    if status == "fail" and abs(target.value) >= 100 and target.value % 10 == 0 and abs(diff) < 10:
        # Bills print "round sum payable" amounts rounded to the nearest 10.
        status = "rounding"
        notes.append("the stated amount is rounded to a multiple of 10")
    details = {
        "expression": f"{expr} = {fmt(round(expected, 6))}",
        "stated": target.label,
        "target_ref": target.refs,
        "operand_refs": [r for _, op in ops for r in op.refs],
    }
    if notes:
        details["note"] = "; ".join(notes)
    return CheckResult(rel.description, status, round(expected, 6), target.value, round(diff, 6), "extractor", details)


def _meter_reading(ops: list[tuple[int, _Operand]], factor: float, target: _Operand) -> tuple[float, str, str, float] | None:
    """Try (a - b) x c for each choice of the multiplying factor c; return the first reading that fits."""
    vals = [op for _, op in ops]
    for i, c in enumerate(vals):
        a, b = sorted((v for j, v in enumerate(vals) if j != i), key=lambda v: -v.value)
        expected = (a.value - b.value) * c.value * factor
        slack = (a.step + b.step) * abs(c.value * factor) / 2
        status, diff = classify(expected, target.value, target.step, slack)
        if status != "fail":
            expr = f"({fmt(a.value)} - {fmt(b.value)}) x {fmt(c.value)}" + (f" x {fmt(factor)}" if factor != 1.0 else "")
            return expected, expr, status, diff
    return None


def table_total_checks(cells: list[Cell]) -> list[CheckResult]:
    """Find rows labelled TOTAL/ANNUAL/... in column A and compare each additive numeric column
    with the sum of the data rows directly above (skipping subtotal rows)."""
    results: list[CheckResult] = []
    by_sheet: dict[str, dict[int, dict[int, Cell]]] = {}
    for c in cells:
        by_sheet.setdefault(c.sheet, {}).setdefault(c.row, {})[c.col] = c
    for sheet, rows in by_sheet.items():
        for r in sorted(rows):
            label_cell = rows[r].get(1)
            if not label_cell or label_cell.number is not None or not _is_grand_total(label_cell.value):
                continue
            block = _data_block_above(rows, r)
            if len(block) < 2:
                continue
            header_row = _header_row(rows, block[0])
            for col, tcell in sorted(rows[r].items()):
                if col == 1 or tcell.number is None or "%" in tcell.value:
                    continue
                header = (rows.get(header_row, {}).get(col).value if header_row and rows.get(header_row, {}).get(col) else "")
                if NON_ADDITIVE_HEADER.search(header or ""):
                    continue
                operands = [rows[b][col] for b in block if col in rows[b] and rows[b][col].number is not None]
                if len(operands) < 2:
                    continue
                expected = sum(c.number for c in operands)
                step = 10 ** -decimals(tcell.value)
                slack = sum(10 ** -decimals(c.value) for c in operands) / 2
                status, diff = classify(expected, tcell.number, step, slack)
                first, last = operands[0].ref, operands[-1].ref
                results.append(
                    CheckResult(
                        f"{sheet}: '{label_cell.value}' {header or tcell.ref} equals the sum of {first}:{last}",
                        status,
                        expected,
                        tcell.number,
                        round(diff, 6),
                        "table_totals",
                        {
                            "expression": f"sum({sheet}!{first}:{last}) = {fmt(expected)}",
                            "stated": f"{sheet}!{tcell.ref} = {tcell.value}",
                            "target_ref": [f"{sheet}!{tcell.ref}"],
                            "operand_refs": [f"{sheet}!{first}:{last}"],
                        },
                    )
                )
    return results


def _is_grand_total(label: str) -> bool:
    """"TOTAL", "Annual Total", "Grand total 2026" are grand totals. "Total Renewable" totals a
    subset of the rows above, which only the extractor's declared relations can check."""
    if not TOTAL_LABEL.search(label):
        return False
    rest = re.sub(r"\b(grand|overall|annual|total|sum|all|year|full)\b|\d+|[^a-z]", "", label.lower())
    return rest == ""


def _has_number(row: dict[int, Cell]) -> bool:
    return any(c.number is not None and c.col != 1 for c in row.values())


def _data_block_above(rows: dict[int, dict[int, Cell]], total_row: int) -> list[int]:
    r = total_row - 1
    while r > 0 and r not in rows:  # skip blank spacer rows
        r -= 1
    block = []
    while r > 0 and r in rows and _has_number(rows[r]):
        label = rows[r].get(1)
        if label and label.number is None and TOTAL_LABEL.search(label.value):
            r -= 1
            continue  # subtotal rows are not summed into the grand total
        block.append(r)
        r -= 1
    return sorted(block)


def _header_row(rows: dict[int, dict[int, Cell]], first_data_row: int) -> int | None:
    r = first_data_row - 1
    while r > 0:
        if r in rows and not _has_number(rows[r]):
            return r
        r -= 1
    return None
