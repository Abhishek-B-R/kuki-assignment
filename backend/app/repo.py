"""Read-side queries shared by the API, Q&A and evals."""

from __future__ import annotations

import sqlite3
from typing import Any

from .db import row_to_dict
from .parsing.sheets import Cell

DOC_COLUMNS = (
    "id, filename, sha256, mime, size_bytes, status, stage, error, attempts, doc_type, title, organisation, issuer,"
    " summary, periods_json, gaps_json, anomalies_json, page_count, pipeline_version, created_at, updated_at, processed_at"
)


def list_documents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT {DOC_COLUMNS} FROM documents ORDER BY created_at, filename").fetchall()
    docs = [row_to_dict(r) for r in rows]
    counts = {
        r["document_id"]: dict(r)
        for r in conn.execute(
            "SELECT document_id, COUNT(*) facts, SUM(evidence_status='verified') verified FROM facts GROUP BY document_id"
        )
    }
    fails = {
        r["document_id"]: r["n"]
        for r in conn.execute("SELECT document_id, COUNT(*) n FROM checks WHERE status='fail' GROUP BY document_id")
    }
    for d in docs:
        c = counts.get(d["id"], {})
        d["fact_count"] = c.get("facts", 0)
        d["verified_count"] = c.get("verified", 0) or 0
        d["failed_checks"] = fails.get(d["id"], 0)
    return docs


def get_document(conn: sqlite3.Connection, doc_id: str) -> dict[str, Any] | None:
    return row_to_dict(conn.execute(f"SELECT {DOC_COLUMNS}, path FROM documents WHERE id=?", (doc_id,)).fetchone())


def facts_for(conn: sqlite3.Connection, doc_ids: list[str] | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT f.*, d.filename, d.organisation, d.doc_type FROM facts f JOIN documents d ON d.id=f.document_id"
        " WHERE d.status='succeeded'"
    )
    args: list[Any] = []
    if doc_ids is not None:
        sql += f" AND f.document_id IN ({','.join('?' * len(doc_ids))})"
        args = list(doc_ids)
    sql += " ORDER BY d.created_at, f.rowid"
    return [row_to_dict(r) for r in conn.execute(sql, args)]


def checks_for(conn: sqlite3.Connection, doc_ids: list[str]) -> list[dict[str, Any]]:
    if not doc_ids:
        return []
    rows = conn.execute(
        f"SELECT * FROM checks WHERE document_id IN ({','.join('?' * len(doc_ids))}) ORDER BY rowid", doc_ids
    )
    return [row_to_dict(r) for r in rows]


def pages_for(conn: sqlite3.Connection, doc_id: str) -> dict[int, str]:
    return {r["page_no"]: r["text"] for r in conn.execute("SELECT page_no, text FROM pages WHERE document_id=?", (doc_id,))}


def verify_pages_for(conn: sqlite3.Connection, doc_id: str) -> dict[int, str]:
    """Page text in both orderings, for checking quotes."""
    rows = conn.execute("SELECT page_no, text, reading_text FROM pages WHERE document_id=?", (doc_id,))
    return {r["page_no"]: f"{r['text']}\n{r['reading_text']}" for r in rows}


def cells_for(conn: sqlite3.Connection, doc_id: str) -> list[Cell]:
    return [
        Cell(r["sheet"], r["ref"], r["row"], r["col"], r["value"], r["number"], r["formula"])
        for r in conn.execute("SELECT * FROM cells WHERE document_id=? ORDER BY sheet, row, col", (doc_id,))
    ]


def events_for(conn: sqlite3.Connection, doc_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ts, level, message FROM events WHERE document_id=? ORDER BY id DESC LIMIT ?", (doc_id, limit)
    ).fetchall()
    return [dict(r) for r in reversed(rows)]


def ready_ids(conn: sqlite3.Connection, doc_ids: list[str] | None) -> tuple[list[str], list[dict]]:
    """Split a selection into processed documents and ones that are not ready (with status)."""
    docs = list_documents(conn)
    if doc_ids is not None:
        wanted = set(doc_ids)
        docs = [d for d in docs if d["id"] in wanted]
    ready = [d["id"] for d in docs if d["status"] == "succeeded"]
    pending = [{"id": d["id"], "filename": d["filename"], "status": d["status"]} for d in docs if d["status"] != "succeeded"]
    return ready, pending
