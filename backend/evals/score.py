"""Deterministic scoring of one answer against one eval case. No model is involved, so scores are
reproducible and a regression in the scorer cannot hide behind judge noise."""

from __future__ import annotations

import re
from typing import Any

from app.numbers import numbers_in
from app.parsing.sheets import split_ref
from openpyxl.utils import range_boundaries

WEIGHTS = {"answer": 0.35, "status": 0.15, "evidence": 0.2, "uncertainty": 0.2, "calculation": 0.1}
PASS_AT = 0.8


# Models write typographic dashes and spaces ("End‑to‑End" with U+2011, "Jan–Mar"); match them as ASCII.
_PLAIN = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"} | {c: " " for c in "\u00a0\u202f\u2009"})


def _plain(text: str) -> str:
    return text.translate(_PLAIN)


def _haystack_numbers(text: str) -> list[float]:
    return numbers_in(text)


def _has_number(target: float, nums: list[float]) -> bool:
    tol = max(0.005, abs(target) * 1e-9)
    return any(abs(n - target) <= tol for n in nums)


def _item_found(alternatives: list, text: str, nums: list[float]) -> bool:
    low = _plain(text).lower()
    for alt in alternatives:
        if isinstance(alt, (int, float)) and not isinstance(alt, bool):
            if _has_number(float(alt), nums):
                return True
        elif _plain(str(alt)).lower() in low:
            return True
    return False


def _answer_text(ans: dict) -> str:
    kv = " ".join(f"{k['label']}: {k['value']} {k.get('unit') or ''}" for k in ans.get("key_values", []))
    return f"{ans.get('answer', '')}\n{kv}"


def _flag_text(ans: dict) -> str:
    parts = [ans.get("answer", "")] + ans.get("gaps", []) + ans.get("inconsistencies", []) + ans.get("assumptions", [])
    return "\n".join(parts)


def _ranges_overlap(a: str, b: str) -> bool:
    try:
        a0, a1, a2, a3 = range_boundaries(a)
        b0, b1, b2, b3 = range_boundaries(b)
    except ValueError:
        return False
    return not (a2 < b0 or b2 < a0 or a3 < b1 or b3 < a1)


def _source_hit(src: dict, citations: list[dict]) -> bool:
    for c in citations:
        if c.get("verification") not in ("verified", "partial") or c.get("filename") != src["doc"]:
            continue
        if "pages" in src and c.get("page") in src["pages"]:
            return True
        if "sheet" in src and c.get("sheet") == src["sheet"]:
            rng = c.get("cell_range") or ""
            parsed = split_ref(rng, c.get("sheet"))
            if parsed and _ranges_overlap(parsed[1], src["cells"]):
                return True
    return False


def score_case(case: dict, ans: dict) -> dict[str, Any]:
    text = _plain(_answer_text(ans))
    nums = _haystack_numbers(text)
    flags_text = _plain(_flag_text(ans))
    flag_nums = _haystack_numbers(flags_text)
    detail: dict[str, Any] = {}
    s: dict[str, float | None] = {}

    inc = case.get("must_include") or []
    found = [_item_found(item, text, nums) for item in inc]
    s["answer"] = sum(found) / len(found) if inc else None
    detail["missing"] = [item for item, ok in zip(inc, found) if not ok]

    violations = [p for p in case.get("must_not") or [] if re.search(p, ans.get("answer", ""), re.I)]
    detail["violations"] = violations
    if violations and s["answer"] is not None:
        s["answer"] = 0.0

    s["status"] = 1.0 if ans.get("status") in case["expected_status"] else 0.0
    detail["status"] = ans.get("status")

    cites = ans.get("citations", [])
    srcs = case.get("sources") or []
    hits = [_source_hit(src, cites) for src in srcs]
    supported = sum(c.get("verification") in ("verified", "partial") for c in cites)
    precision = supported / len(cites) if cites else 0.0
    recall = sum(hits) / len(hits) if srcs else None
    s["evidence"] = None if recall is None else round(0.7 * recall + 0.3 * precision, 4)
    detail["evidence"] = {"sources_hit": sum(hits), "sources": len(srcs), "citations": len(cites), "supported": supported}
    detail["sources_missed"] = [src for src, ok in zip(srcs, hits) if not ok]

    flags = case.get("must_flag") or []
    fl = [_item_found(item, flags_text, flag_nums) for item in flags]
    s["uncertainty"] = sum(fl) / len(fl) if flags else None
    detail["flags_missing"] = [item for item, ok in zip(flags, fl) if not ok]

    calcs = ans.get("calculations", [])
    if case.get("calculation"):
        s["calculation"] = 1.0 if calcs and all(c.get("ok") for c in calcs) else 0.0
    elif calcs:
        s["calculation"] = 1.0 if all(c.get("ok") for c in calcs) else 0.0
    else:
        s["calculation"] = None
    detail["calculations"] = [{"expression": c["expression"], "result": c["result"], "ok": c.get("ok")} for c in calcs]

    applicable = {k: v for k, v in s.items() if v is not None}
    weight = sum(WEIGHTS[k] for k in applicable)
    overall = sum(WEIGHTS[k] * v for k, v in applicable.items()) / weight if weight else 0.0
    return {
        "scores": {k: (round(v, 4) if v is not None else None) for k, v in s.items()},
        "overall": round(overall, 4),
        "passed": overall >= PASS_AT and not violations and s["status"] == 1.0,
        "detail": detail,
    }


def summarise(results: list[dict]) -> dict[str, Any]:
    dims = list(WEIGHTS)
    out: dict[str, Any] = {"cases": len(results), "passed": sum(r["score"]["passed"] for r in results)}
    out["overall"] = round(sum(r["score"]["overall"] for r in results) / len(results), 4) if results else 0
    for d in dims:
        vals = [r["score"]["scores"][d] for r in results if r["score"]["scores"].get(d) is not None]
        out[d] = round(sum(vals) / len(vals), 4) if vals else None
    return out
