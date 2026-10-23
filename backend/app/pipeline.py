"""Process one document: parse -> extract (LLM) -> verify evidence -> run checks -> save.

Saving replaces everything previously stored for the document inside one transaction, and
only if this worker still owns the job, so retries and reprocessing are idempotent.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import jobqueue
from .checks import CheckResult, evaluate_relation, table_total_checks
from .db import log_event, now, transaction
from .jobqueue import Job
from .llm import LLM, Blob, Part
from .parsing.pdf import IMAGE_ONLY_WORDS, PdfError, PdfPage, find_boxes, parse_pdf
from .parsing.sheets import Cell, SheetError, parse_csv, parse_xlsx, render_for_model
from .prompts import EXTRACTION_SYSTEM, SHEET_SERIES_ADDENDUM
from .schemas import ExtractedFact, Extraction, SheetExtraction
from .series import expand_series
from .verify import verify_pdf, verify_sheet

log = logging.getLogger(__name__)

# v1: the model lists every fact. v2: for spreadsheets the model declares per-period series and
# code reads each row from the cells (PDF extraction is identical in both).
EXTRACT_VERSIONS = ("v1", "v2")
MAX_MODEL_TEXT = 300_000

PDF_TYPES = {".pdf"}
SHEET_TYPES = {".xlsx", ".xlsm", ".csv"}
SUPPORTED = PDF_TYPES | SHEET_TYPES


class PermanentError(Exception):
    """The document itself is the problem (corrupt, empty, unsupported). Do not retry."""


class LeaseLost(Exception):
    """Another worker took over this job; drop our results."""


@dataclass
class Parsed:
    kind: str  # pdf | sheet
    pages: list[PdfPage]
    cells: list[Cell]


def parse(path: Path) -> Parsed:
    ext = path.suffix.lower()
    try:
        if ext in PDF_TYPES:
            return Parsed("pdf", parse_pdf(path), [])
        if ext == ".csv":
            return Parsed("sheet", [], parse_csv(path))
        if ext in SHEET_TYPES:
            return Parsed("sheet", [], parse_xlsx(path))
    except (PdfError, SheetError) as exc:
        raise PermanentError(str(exc)) from exc
    raise PermanentError(f"Unsupported file type '{ext}'. Supported: {', '.join(sorted(SUPPORTED))}")


def model_parts(filename: str, path: Path, parsed: Parsed) -> list[Part]:
    if parsed.kind == "pdf":
        layer = []
        for p in parsed.pages:
            body = p.text.strip() if p.word_count >= IMAGE_ONLY_WORDS else (p.text.strip() + "\n(little or no text layer: read this page from the image)")
            layer.append(f"[[PAGE {p.page_no}]]\n{body}")
        text = "\n\n".join(layer)[:MAX_MODEL_TEXT]
        return [
            Blob(path.read_bytes(), "application/pdf"),
            f"File name: {filename}\nPages: {len(parsed.pages)}\n\nText layer by page:\n\n{text}",
        ]
    grid = render_for_model(parsed.cells)[:MAX_MODEL_TEXT]
    return [f"File name: {filename}\nSpreadsheet cells (row by row, with coordinates):\n\n{grid}"]


def extract(llm: LLM, filename: str, path: Path, parsed: Parsed, version: str = "v1") -> Extraction:
    parts = model_parts(filename, path, parsed)
    if version == "v2" and parsed.kind == "sheet":
        ex = llm.generate(system=EXTRACTION_SYSTEM + SHEET_SERIES_ADDENDUM, parts=parts, schema=SheetExtraction, purpose="extraction")
        facts, problems = expand_series(ex.series, parsed.cells)
        ex.facts.extend(facts)
        ex.gaps.extend(problems)
        return ex
    return llm.generate(system=EXTRACTION_SYSTEM, parts=parts, schema=Extraction, purpose="extraction")


@dataclass
class VerifiedFact:
    fact: ExtractedFact
    status: str
    page: int | None
    sheet: str | None
    cell_range: str | None
    bbox: list[list[float]]
    note: str | None


def verify_facts(path: Path, parsed: Parsed, facts: list[ExtractedFact]) -> list[VerifiedFact]:
    out = []
    page_text = {p.page_no: p.verify_text for p in parsed.pages}
    for f in facts:
        if parsed.kind == "pdf":
            ev = verify_pdf(page_text, f.page, f.quote, f.value_num)
            bbox = find_boxes(path, ev.page, [s for s in (f.quote, f.value_text) if s]) if ev.page and ev.status != "unverified" else []
            out.append(VerifiedFact(f, ev.status, ev.page, None, None, bbox, ev.note))
        else:
            ev = verify_sheet(parsed.cells, f.sheet, f.cell_range, f.quote, f.value_num)
            out.append(VerifiedFact(f, ev.status, None, ev.sheet, ev.cell_range, [], ev.note))
    return out


def run_checks(parsed: Parsed, extraction: Extraction) -> list[CheckResult]:
    facts = {f.id: f for f in extraction.facts}
    results = [evaluate_relation(r, facts, parsed.cells) for r in extraction.relations]
    if parsed.kind == "sheet":
        results.extend(table_total_checks(parsed.cells))
    return results


def process_document(
    db: Callable[[], sqlite3.Connection],
    llm: LLM,
    job: Job,
    lease_s: float,
    version: str = "v1",
) -> None:
    """Run the pipeline for a claimed job. Raises PermanentError / TransientLLMError / LeaseLost."""
    conn = db()
    try:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (job.document_id,)).fetchone()
        if doc is None:
            raise PermanentError("Document was deleted")
        path = Path(doc["path"])
        if not path.exists():
            raise PermanentError("Stored file is missing")

        _stage(conn, job, "parsing", lease_s)
        parsed = parse(path)
        _save_parsed(conn, job, parsed)
        log_event(conn, job.document_id, f"Parsed {len(parsed.pages)} pages" if parsed.kind == "pdf" else f"Parsed {len(parsed.cells)} cells")

        _stage(conn, job, "extracting", lease_s)
        extraction = extract(llm, doc["filename"], path, parsed, version)
        log_event(conn, job.document_id, f"Extracted {len(extraction.facts)} facts as {extraction.doc_type}")

        _stage(conn, job, "verifying", lease_s)
        verified = verify_facts(path, parsed, extraction.facts)
        checks = run_checks(parsed, extraction)
        n_ok = sum(v.status == "verified" for v in verified)
        n_fail = sum(c.status == "fail" for c in checks)
        log_event(conn, job.document_id, f"Evidence verified for {n_ok}/{len(verified)} facts; {len(checks)} checks, {n_fail} failed")

        _stage(conn, job, "saving", lease_s)
        _save_results(conn, job, parsed, extraction, verified, checks, f"extract-{version}")
    finally:
        conn.close()


def _stage(conn: sqlite3.Connection, job: Job, stage: str, lease_s: float) -> None:
    if not jobqueue.heartbeat(conn, job, lease_s):
        raise LeaseLost()
    conn.execute("UPDATE documents SET stage=?, updated_at=? WHERE id=?", (stage, now(), job.document_id))


def _save_parsed(conn: sqlite3.Connection, job: Job, parsed: Parsed) -> None:
    with transaction(conn):
        if not jobqueue.owns(conn, job):
            raise LeaseLost()
        conn.execute("DELETE FROM pages WHERE document_id=?", (job.document_id,))
        conn.execute("DELETE FROM cells WHERE document_id=?", (job.document_id,))
        conn.executemany(
            "INSERT INTO pages(document_id, page_no, text, reading_text, word_count, width, height) VALUES (?,?,?,?,?,?,?)",
            [(job.document_id, p.page_no, p.text, p.reading_text, p.word_count, p.width, p.height) for p in parsed.pages],
        )
        conn.executemany(
            "INSERT INTO cells(document_id, sheet, ref, row, col, value, number, formula) VALUES (?,?,?,?,?,?,?,?)",
            [(job.document_id, c.sheet, c.ref, c.row, c.col, c.value, c.number, c.formula) for c in parsed.cells],
        )
        conn.execute(
            "UPDATE documents SET page_count=?, updated_at=? WHERE id=?",
            (len(parsed.pages) or None, now(), job.document_id),
        )


def _save_results(
    conn: sqlite3.Connection,
    job: Job,
    parsed: Parsed,
    ex: Extraction,
    verified: list[VerifiedFact],
    checks: list[CheckResult],
    pipeline_version: str,
) -> None:
    with transaction(conn):
        if not jobqueue.owns(conn, job):
            raise LeaseLost()
        conn.execute("DELETE FROM facts WHERE document_id=?", (job.document_id,))
        conn.execute("DELETE FROM checks WHERE document_id=?", (job.document_id,))
        conn.executemany(
            "INSERT INTO facts(id, document_id, local_id, key, label, category, metric, dimension, is_primary, value_text,"
            " value_num, unit, period_start, period_end, origin, page, sheet, cell_range, quote, evidence_status, bbox_json, notes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    uuid.uuid4().hex,
                    job.document_id,
                    v.fact.id,
                    v.fact.key,
                    v.fact.label,
                    v.fact.category,
                    v.fact.metric,
                    v.fact.dimension,
                    int(v.fact.primary),
                    v.fact.value_text,
                    v.fact.value_num,
                    v.fact.unit,
                    v.fact.period_start,
                    v.fact.period_end,
                    "reported",
                    v.page,
                    v.sheet,
                    v.cell_range,
                    v.fact.quote,
                    v.status,
                    json.dumps(v.bbox) if v.bbox else None,
                    _join_notes(v.fact.notes, v.note),
                )
                for v in verified
            ],
        )
        conn.executemany(
            "INSERT INTO checks(id, document_id, name, status, expected, actual, difference, source, details_json)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (uuid.uuid4().hex, job.document_id, c.name, c.status, c.expected, c.actual, c.difference, c.source, json.dumps(c.details))
                for c in checks
            ],
        )
        ts = now()
        conn.execute(
            "UPDATE documents SET status='succeeded', stage='done', error=NULL, doc_type=?, title=?, organisation=?, issuer=?,"
            " summary=?, periods_json=?, gaps_json=?, anomalies_json=?, pipeline_version=?, extraction_json=?, processed_at=?,"
            " updated_at=? WHERE id=?",
            (
                ex.doc_type,
                ex.title,
                ex.organisation,
                ex.issuer,
                ex.summary,
                json.dumps([p.model_dump() for p in ex.reporting_periods]),
                json.dumps(ex.gaps),
                json.dumps([a.model_dump() for a in ex.anomalies]),
                pipeline_version,
                ex.model_dump_json(),
                ts,
                ts,
                job.document_id,
            ),
        )
        if not jobqueue.complete(conn, job):
            raise LeaseLost()
        log_event(conn, job.document_id, "Processing succeeded")


def _join_notes(*notes: str | None) -> str | None:
    parts = [n for n in notes if n]
    return "; ".join(parts) if parts else None
