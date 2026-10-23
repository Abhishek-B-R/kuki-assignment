"""Evidence-based question answering over selected documents.

1. Build an evidence pack: document metadata, extracted facts (with sources), our arithmetic
   checks, a financial-year report when the question names an FY, and the most relevant page text.
2. Ask the model for a structured answer that cites the pack.
3. Verify every citation against the stored page text / cells and re-evaluate every
   calculation, so the UI can show which parts of the answer are actually supported.
"""

from __future__ import annotations

import ast
import json
import operator
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any

from . import repo
from .fy import FinancialYear, fy_report, parse_fy
from .llm import LLM
from .numbers import close, fmt, parse_number
from .prompts import QA_SYSTEM_V1
from .schemas import Answer
from .verify import verify_pdf, verify_sheet

STOP = set(
    "the a an and or of for to in on at by with is are was were be been what which who whom how does do did any all "
    "each this that these those from as it its their there than then into per vs versus about between report reported "
    "document documents show shows give".split()
)
MAX_PAGES = 6


@dataclass
class Pack:
    text: str
    doc_ids: dict[str, str]  # short id (D1) -> document uuid
    fact_ids: dict[str, dict]  # short id (F1) -> fact row
    docs: dict[str, dict]  # uuid -> document row
    fy: dict | None
    pages_used: list[tuple[str, int]] = field(default_factory=list)


def _terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP]


def _fact_line(sid: str, f: dict) -> str:
    loc = f"p{f['page']}" if f.get("page") else (f"{f['sheet']}!{f['cell_range']}" if f.get("cell_range") else "?")
    period = ""
    if f.get("period_start") or f.get("period_end"):
        period = f" period={f.get('period_start') or '?'}..{f.get('period_end') or '?'}"
    extras = "".join(
        f" {k}={f[k]}" for k in ("metric", "dimension") if f.get(k)
    )
    prim = "" if f.get("is_primary", 1) else " secondary"
    note = f" notes={f['notes']!r}" if f.get("notes") else ""
    return (
        f"[{sid}] {f['key']} | {f.get('label')!r} = {f.get('value_text')!r}"
        f"{' ' + f['unit'] if f.get('unit') else ''}{period}{extras}{prim} | source {loc} quote={f.get('quote')!r}"
        f" evidence={f['evidence_status']}{note}"
    )


def build_pack(conn: sqlite3.Connection, question: str, doc_ids: list[str], fy: FinancialYear | None) -> Pack:
    docs = {d: repo.get_document(conn, d) for d in doc_ids}
    facts = repo.facts_for(conn, doc_ids)
    checks = repo.checks_for(conn, doc_ids)
    short_docs = {f"D{i + 1}": d for i, d in enumerate(doc_ids)}
    rev_docs = {v: k for k, v in short_docs.items()}
    short_facts: dict[str, dict] = {}
    lines = []
    for sd, did in short_docs.items():
        d = docs[did]
        lines.append(f"## Document {sd}: {d['filename']}")
        lines.append(f"type={d['doc_type']} organisation={d['organisation']!r} issuer={d['issuer']!r}")
        lines.append(f"title={d['title']!r}")
        lines.append(f"summary: {d['summary']}")
        if d.get("periods"):
            lines.append("reporting periods: " + "; ".join(f"{p['label']} ({p['start']}..{p['end']})" for p in d["periods"]))
        if d.get("gaps"):
            lines.append("gaps noted at extraction: " + "; ".join(d["gaps"]))
        if d.get("anomalies"):
            lines.append("anomalies noted at extraction: " + "; ".join(a["description"] for a in d["anomalies"]))
        lines.append("facts:")
        for f in facts:
            if f["document_id"] == did:
                sid = f"F{len(short_facts) + 1}"
                short_facts[sid] = f
                lines.append("  " + _fact_line(sid, f))
        doc_checks = [c for c in checks if c["document_id"] == did]
        if doc_checks:
            lines.append("arithmetic checks computed by our software (expected = calculated by us, actual = stated in document):")
            for c in doc_checks:
                det = c.get("details") or {}
                lines.append(
                    f"  - [{c['status']}] {c['name']}: {det.get('expression', '')}; stated {det.get('stated', fmt(c['actual']))};"
                    f" difference {fmt(c['difference'])}"
                )
        lines.append("")

    fy_data = None
    if fy is not None:
        fy_data = fy_report(facts, fy)
        lines.append(f"## Financial-year report computed by our software for {fy.label} ({fy.start} to {fy.end})")
        lines.append(json.dumps(_compact_fy(fy_data, rev_docs), ensure_ascii=False))
        lines.append("")

    # Page text retrieval: rank PDF pages by overlap with the question terms.
    q = set(_terms(question))
    scored = []
    for did in doc_ids:
        for page_no, text in repo.pages_for(conn, did).items():
            words = _terms(text)
            if not words:
                continue
            hits = sum(1 for w in words if w in q)
            scored.append((hits / (len(words) ** 0.5), did, page_no, text))
    scored.sort(key=lambda x: -x[0])
    used = []
    for score, did, page_no, text in scored[:MAX_PAGES]:
        if score <= 0:
            break
        used.append((did, page_no))
        lines.append(f"## Page text: document {rev_docs[did]} page {page_no}")
        lines.append(text.strip()[:6000])
        lines.append("")

    return Pack("\n".join(lines), short_docs, short_facts, docs, fy_data, used)


def _compact_fy(rep: dict, rev_docs: dict[str, str]) -> dict:
    def src(s: dict) -> dict:
        return {
            "document": rev_docs.get(s["document_id"]),
            "page": s.get("page"),
            "sheet": s.get("sheet"),
            "cell_range": s.get("cell_range"),
            "quote": s.get("quote"),
        }

    groups = []
    for g in rep["groups"]:
        groups.append(
            {
                "organisation": g["organisation"],
                "metric": g["metric"],
                "dimension": g["dimension"],
                "unit": g["unit"],
                "total_calculated": g["total"],
                "calculation": g["calculation"],
                "months_covered": f"{g['coverage']['months_covered']} of 12",
                "months_missing": g["coverage"]["months_missing"],
                "months_partial": g["coverage"]["months_partial"],
                "includes_prorated": g["includes_prorated"],
                "records": [
                    {"label": r["label"], "value": r["value"], "period": f"{r['period_start']}..{r['period_end']}",
                     "included_share": r["share"], "source": src(r["source"])}
                    for r in g["records"]
                ],
                "cross_checks": [
                    {"description": c["description"], "stated": c["stated"], "sum_of_parts": c["sum_of_parts"],
                     "difference": c["difference"], "status": c["status"], "source": src(c["stated_ref"])}
                    for c in g["cross_checks"]
                ],
            }
        )
    return {
        "financial_year": rep["financial_year"],
        "groups": groups,
        "excluded": [{"label": e["label"], "value": e["value"], "reason": e["reason"]} for e in rep["excluded"]],
        "documents_with_data_only_in_other_years": rep["documents_outside_fy"],
        "notes": rep["notes"],
    }


# ---- verification of the model's answer -------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def safe_eval(expr: str) -> float | None:
    """Evaluate plain arithmetic (numbers, + - * /, parentheses). Anything else returns None."""
    cleaned = re.sub(r"(?<=[\d)\s])[x×](?=[\s\d(])", "*", expr).replace("−", "-")
    cleaned = re.sub(r"(?<=\d),(?=\d)", "", cleaned)
    cleaned = cleaned.split("=")[0]
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError:
        return None

    def ev(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        raise ValueError("unsupported expression")

    try:
        return ev(tree)
    except (ValueError, ZeroDivisionError):
        return None


def verify_answer(conn: sqlite3.Connection, ans: Answer, pack: Pack) -> dict[str, Any]:
    page_cache: dict[str, dict[int, str]] = {}
    cell_cache: dict[str, list] = {}
    citations = []
    for c in ans.citations:
        fact = pack.fact_ids.get((c.fact_id or "").strip())
        doc_id = pack.doc_ids.get(c.document_id.strip()) or (fact["document_id"] if fact else None)
        entry: dict[str, Any] = {
            "document_id": doc_id,
            "filename": pack.docs[doc_id]["filename"] if doc_id in pack.docs else None,
            "page": c.page,
            "sheet": c.sheet,
            "cell_range": c.cell_range,
            "quote": c.quote,
            "fact_id": fact["id"] if fact else None,
        }
        if doc_id is None or doc_id not in pack.docs:
            entry.update(verification="unverified", note="unknown document")
            citations.append(entry)
            continue
        if fact:
            entry["page"] = entry["page"] or fact.get("page")
            entry["sheet"] = entry["sheet"] or fact.get("sheet")
            entry["cell_range"] = entry["cell_range"] or fact.get("cell_range")
        if entry["sheet"] or entry["cell_range"]:
            cells = cell_cache.setdefault(doc_id, repo.cells_for(conn, doc_id))
            ev = verify_sheet(cells, entry["sheet"], entry["cell_range"], c.quote, parse_number(c.quote))
            entry["sheet"], entry["cell_range"] = ev.sheet, ev.cell_range
        else:
            pages = page_cache.setdefault(doc_id, repo.verify_pages_for(conn, doc_id))
            ev = verify_pdf(pages, entry["page"], c.quote, None)
            entry["page"] = ev.page
        status = ev.status
        if status == "unverified" and fact and fact["evidence_status"] == "verified":
            # The quote was paraphrased, but it points at a fact whose own evidence checked out.
            status, ev.note = "partial", "quote paraphrased; linked fact is verified"
        entry.update(verification=status, note=ev.note)
        if fact and fact.get("bbox") and entry["page"] == fact.get("page"):
            entry["bbox"] = fact["bbox"]
        citations.append(entry)

    calcs = []
    for calc in ans.calculations:
        value = safe_eval(calc.expression)
        ok = value is not None and close(value, calc.result, rel=1e-4, abs_=0.01)
        calcs.append({**calc.model_dump(), "recomputed": value, "ok": ok})

    out = ans.model_dump()
    out["citations"] = citations
    out["calculations"] = calcs
    out["support"] = {
        "citations": len(citations),
        "verified": sum(c["verification"] == "verified" for c in citations),
        "partial": sum(c["verification"] == "partial" for c in citations),
        "unverified": sum(c["verification"] == "unverified" for c in citations),
        "calculations_ok": sum(c["ok"] for c in calcs),
        "calculations": len(calcs),
    }
    return out


def ask(
    conn: sqlite3.Connection,
    llm: LLM,
    question: str,
    doc_ids: list[str] | None = None,
    financial_year: str | None = None,
) -> dict[str, Any]:
    t0 = time.time()
    ready, pending = repo.ready_ids(conn, doc_ids)
    fy = parse_fy(financial_year) if financial_year else parse_fy(question)
    base = {
        "question": question,
        "financial_year": fy.label if fy else None,
        "documents": [{"id": d, "filename": repo.get_document(conn, d)["filename"]} for d in ready],
        "not_ready": pending,
    }
    if not ready:
        return {
            **base,
            "status": "not_found",
            "answer": "None of the selected documents have been processed yet, so there is no evidence to answer from.",
            "key_values": [], "calculations": [], "citations": [], "gaps": ["No processed documents selected"],
            "inconsistencies": [], "assumptions": [], "support": {}, "fy_report": None, "elapsed_s": 0.0,
        }
    pack = build_pack(conn, question, ready, fy)
    system = QA_SYSTEM_V1
    asked_for = f"\nFinancial year: {fy.label} ({fy.start} to {fy.end})" if fy else ""
    prompt = f"Question: {question}{asked_for}\n\n# Evidence\n\n{pack.text}"
    if pending:
        prompt += "\nNot yet processed (no evidence available): " + ", ".join(p["filename"] for p in pending)
    ans = llm.generate(system=system, parts=[prompt], schema=Answer, purpose="answer")
    verified = verify_answer(conn, ans, pack)
    return {**base, **verified, "fy_report": pack.fy, "elapsed_s": round(time.time() - t0, 2)}
