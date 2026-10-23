"""Number and unit handling shared by verification, checks, financial-year totals and evals."""

from __future__ import annotations

import math
import re

# 1,23,456.78 (Indian grouping), 123,456.78, 123456, -8,777.32, .5
_NUM = re.compile(r"(?<![\w.])-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?(?![\d])|(?<![\w\d])-?\.\d+")


def parse_number(text: str | None) -> float | None:
    """Parse a single number written in a document ("₹1,51,174.88", "34.0%", "1.0 T").
    Returns None when the text does not hold exactly one number."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    found = _NUM.findall(s.replace("−", "-"))
    if len(found) != 1:
        return None
    try:
        return float(found[0].replace(",", ""))
    except ValueError:
        return None


def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text.replace("−", "-")):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def close(a: float, b: float, rel: float = 1e-6, abs_: float = 1e-6) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def matches_value(target: float, candidates: list[float]) -> bool:
    """True when a candidate equals target as written, allowing for rounding to the
    precision shown (47407 vs 47407.2 does not match; 631510 vs 631510.00 does)."""
    return any(close(target, c, rel=1e-9, abs_=0.005) for c in candidates)


_UNITS = {
    "kwh": ("kWh", 1.0),
    "units": ("kWh", 1.0),
    "unit": ("kWh", 1.0),
    "mwh": ("kWh", 1000.0),
    "gwh": ("kWh", 1_000_000.0),
    "m3": ("m3", 1.0),
    "m³": ("m3", 1.0),
    "cu.m": ("m3", 1.0),
    "cum": ("m3", 1.0),
    "cubic meters": ("m3", 1.0),
    "cubic metres": ("m3", 1.0),
    "kl": ("m3", 1.0),
    "kilolitres": ("m3", 1.0),
    "kiloliters": ("m3", 1.0),
    "litres": ("m3", 0.001),
    "liters": ("m3", 0.001),
    "l": ("m3", 0.001),
    "mt": ("t", 1.0),
    "t": ("t", 1.0),
    "tonnes": ("t", 1.0),
    "tons": ("t", 1.0),
    "tonne": ("t", 1.0),
    "kg": ("t", 0.001),
    "inr": ("INR", 1.0),
    "rs": ("INR", 1.0),
    "rs.": ("INR", 1.0),
    "₹": ("INR", 1.0),
    "rupees": ("INR", 1.0),
    "kva": ("kVA", 1.0),
    "lakh": ("INR", 100_000.0),
    "lakh inr": ("INR", 100_000.0),
    "inr lakh": ("INR", 100_000.0),
    "rs lakh": ("INR", 100_000.0),
    "₹ lakh": ("INR", 100_000.0),
    "crore": ("INR", 10_000_000.0),
    "crore inr": ("INR", 10_000_000.0),
    "inr crore": ("INR", 10_000_000.0),
    "rs crore": ("INR", 10_000_000.0),
    "₹ crore": ("INR", 10_000_000.0),
}


def normalise_unit(unit: str | None) -> tuple[str | None, float]:
    """Map a unit as written to a canonical unit and the factor to convert into it."""
    if not unit:
        return None, 1.0
    key = " ".join(unit.strip().lower().replace("_", " ").split())
    key = re.sub(r"\b(lakh|crore)s\b", r"\1", key)  # "lakhs INR" -> "lakh inr"
    for candidate in (key, key + "s", key.removesuffix("s")):
        if candidate in _UNITS:
            return _UNITS[candidate]
    return unit.strip(), 1.0


def fmt(value: float | None, unit: str | None = None) -> str:
    if value is None:
        return "n/a"
    s = f"{value:,.2f}".rstrip("0").rstrip(".") if not float(value).is_integer() else f"{int(value):,}"
    return f"{s} {unit}".strip() if unit else s
