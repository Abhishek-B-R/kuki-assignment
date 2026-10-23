"""SQLite storage. One file holds documents, jobs, parsed content, extracted facts and checks.

WAL mode lets the API read while worker threads write. Every connection is short-lived and
opened per unit of work, so threads never share one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    sha256        TEXT NOT NULL UNIQUE,
    mime          TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    path          TEXT NOT NULL,
    status        TEXT NOT NULL,          -- queued | processing | retrying | succeeded | failed
    stage         TEXT,                   -- parsing | extracting | verifying | saving | done
    error         TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    doc_type      TEXT,
    title         TEXT,
    organisation  TEXT,
    issuer        TEXT,
    summary       TEXT,
    periods_json  TEXT,
    gaps_json     TEXT,
    anomalies_json TEXT,
    page_count    INTEGER,
    pipeline_version TEXT,
    extraction_json TEXT,                 -- raw model output, so checks can be recomputed offline
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    processed_at  REAL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status       TEXT NOT NULL,           -- queued | running | succeeded | failed
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    deferrals    INTEGER NOT NULL DEFAULT 0,   -- rate-limit postponements (not counted as attempts)
    run_after    REAL NOT NULL,
    lease_until  REAL,
    worker_id    TEXT,
    last_error   TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);
-- At most one live job per document: duplicate uploads or double clicks cannot process twice.
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active
    ON jobs(document_id) WHERE status IN ('queued', 'running');
CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(status, run_after);

CREATE TABLE IF NOT EXISTS pages (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    reading_text TEXT NOT NULL DEFAULT '',
    word_count  INTEGER NOT NULL,
    width       REAL,
    height      REAL,
    PRIMARY KEY (document_id, page_no)
);

CREATE TABLE IF NOT EXISTS cells (
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    sheet       TEXT NOT NULL,
    ref         TEXT NOT NULL,
    row         INTEGER NOT NULL,
    col         INTEGER NOT NULL,
    value       TEXT,
    number      REAL,
    formula     TEXT,
    PRIMARY KEY (document_id, sheet, ref)
);

CREATE TABLE IF NOT EXISTS facts (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    local_id      TEXT,                  -- id the extractor used (f1, f2 ...), for relations
    key           TEXT NOT NULL,
    label         TEXT,
    category      TEXT NOT NULL,
    metric        TEXT,
    dimension     TEXT,
    is_primary    INTEGER NOT NULL DEFAULT 1,  -- 0 for history/previous-period/average values
    value_text    TEXT,
    value_num     REAL,
    unit          TEXT,
    period_start  TEXT,
    period_end    TEXT,
    origin        TEXT NOT NULL,          -- reported (read from the document) | calculated (by us)
    page          INTEGER,
    sheet         TEXT,
    cell_range    TEXT,
    quote         TEXT,
    evidence_status TEXT NOT NULL,        -- verified | partial | unverified
    bbox_json     TEXT,
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS facts_doc ON facts(document_id);

CREATE TABLE IF NOT EXISTS checks (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL,           -- pass | rounding | fail | skipped
    expected     REAL,
    actual       REAL,
    difference   REAL,
    source       TEXT NOT NULL,           -- extractor | table_totals
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS checks_doc ON checks(document_id);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    level       TEXT NOT NULL,
    message     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_doc ON events(document_id, ts);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# Columns added after the first schema. CREATE TABLE IF NOT EXISTS leaves old tables alone,
# so databases from earlier versions get them here.
MIGRATIONS = [
    ("pages", "reading_text", "TEXT NOT NULL DEFAULT ''"),
    ("jobs", "deferrals", "INTEGER NOT NULL DEFAULT 0"),
    ("documents", "extraction_json", "TEXT"),
]


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        for table, column, ddl in MIGRATIONS:
            cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    finally:
        conn.close()


@contextmanager
def session(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE takes the write lock up front, so check-then-write sequences are atomic."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def now() -> float:
    return time.time()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for k in list(out):
        if k.endswith("_json"):
            raw = out.pop(k)
            out[k[: -len("_json")]] = json.loads(raw) if raw else None
    return out


def log_event(conn: sqlite3.Connection, document_id: str, message: str, level: str = "info") -> None:
    conn.execute(
        "INSERT INTO events(document_id, ts, level, message) VALUES (?,?,?,?)",
        (document_id, now(), level, message),
    )
