"""A durable job queue on SQLite.

Guarantees:
- A document has at most one queued/running job (partial unique index), so duplicate
  uploads and repeated "reprocess" clicks never process the same document twice at once.
- Claims are leases. A worker that crashes or hangs stops renewing its lease; once the lease
  expires another worker reclaims the job. Each claim counts as an attempt.
- Transient failures are retried with exponential backoff and jitter. Permanent failures and
  jobs out of attempts end as `failed` with the last error kept for the UI.
- Every state change after the claim checks the worker still owns the job, so a worker whose
  lease was taken over cannot overwrite the newer attempt's outcome.
"""

from __future__ import annotations

import random
import sqlite3
import uuid
from dataclasses import dataclass

from .db import log_event, now, transaction


@dataclass(frozen=True)
class Job:
    id: str
    document_id: str
    attempts: int
    max_attempts: int
    worker_id: str


def enqueue(conn: sqlite3.Connection, document_id: str, max_attempts: int) -> str | None:
    """Queue a job for the document. Returns the job id, or None if one is already live."""
    job_id = uuid.uuid4().hex
    ts = now()
    try:
        conn.execute(
            "INSERT INTO jobs(id, document_id, status, attempts, max_attempts, run_after, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (job_id, document_id, "queued", 0, max_attempts, ts, ts, ts),
        )
    except sqlite3.IntegrityError:
        return None
    return job_id


def claim(conn: sqlite3.Connection, worker_id: str, lease_s: float) -> Job | None:
    """Atomically take the oldest runnable job: a queued one that is due, or a running one
    whose lease expired (its worker died)."""
    ts = now()
    with transaction(conn):
        row = conn.execute(
            "SELECT * FROM jobs WHERE (status='queued' AND run_after<=?)"
            " OR (status='running' AND lease_until<?) ORDER BY run_after, created_at LIMIT 1",
            (ts, ts),
        ).fetchone()
        if row is None:
            return None
        if row["status"] == "running":
            log_event(conn, row["document_id"], "Previous worker stopped responding; job reclaimed", "warn")
        if row["attempts"] >= row["max_attempts"]:
            # Stale lease on the final attempt: the worker kept dying. Give up cleanly.
            _finish_failed(conn, row["id"], row["document_id"], row["last_error"] or "Worker stopped responding", ts)
            return None
        conn.execute(
            "UPDATE jobs SET status='running', attempts=attempts+1, worker_id=?, lease_until=?, updated_at=? WHERE id=?",
            (worker_id, ts + lease_s, ts, row["id"]),
        )
        conn.execute(
            "UPDATE documents SET status='processing', stage='parsing', attempts=?, error=NULL, updated_at=? WHERE id=?",
            (row["attempts"] + 1, ts, row["document_id"]),
        )
        log_event(conn, row["document_id"], f"Processing started (attempt {row['attempts'] + 1} of {row['max_attempts']})")
        return Job(row["id"], row["document_id"], row["attempts"] + 1, row["max_attempts"], worker_id)


def heartbeat(conn: sqlite3.Connection, job: Job, lease_s: float) -> bool:
    """Extend the lease. False means another worker owns the job now: stop working on it."""
    cur = conn.execute(
        "UPDATE jobs SET lease_until=?, updated_at=? WHERE id=? AND worker_id=? AND status='running'",
        (now() + lease_s, now(), job.id, job.worker_id),
    )
    return cur.rowcount == 1


def owns(conn: sqlite3.Connection, job: Job) -> bool:
    row = conn.execute("SELECT 1 FROM jobs WHERE id=? AND worker_id=? AND status='running'", (job.id, job.worker_id)).fetchone()
    return row is not None


def complete(conn: sqlite3.Connection, job: Job) -> bool:
    cur = conn.execute(
        "UPDATE jobs SET status='succeeded', lease_until=NULL, updated_at=? WHERE id=? AND worker_id=? AND status='running'",
        (now(), job.id, job.worker_id),
    )
    return cur.rowcount == 1


def backoff_seconds(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with full jitter: attempt 1 -> up to base, 2 -> 2x base, ..."""
    return random.uniform(0.5, 1.0) * min(cap, base * (2 ** (attempt - 1)))


def fail(
    conn: sqlite3.Connection,
    job: Job,
    error: str,
    transient: bool,
    retry_base_s: float,
    retry_max_s: float,
    retry_after: float | None = None,
    rate_limited: bool = False,
    max_deferrals: int = 20,
) -> str:
    """Record a failed attempt. Returns the job's new status: 'queued' (will retry), 'failed', or 'lost'.

    A rate-limited attempt is deferred: the attempt is refunded and the job waits at least as long
    as the provider asked, up to `max_deferrals` times."""
    ts = now()
    with transaction(conn):
        row = conn.execute(
            "SELECT * FROM jobs WHERE id=? AND worker_id=? AND status='running'", (job.id, job.worker_id)
        ).fetchone()
        if row is None:
            return "lost"
        if transient and rate_limited and row["deferrals"] < max_deferrals:
            delay = max(retry_after or 0, backoff_seconds(row["deferrals"] + 1, retry_base_s, retry_max_s)) + 1
            conn.execute(
                "UPDATE jobs SET status='queued', attempts=attempts-1, deferrals=deferrals+1, run_after=?, lease_until=NULL,"
                " worker_id=NULL, last_error=?, updated_at=? WHERE id=?",
                (ts + delay, error, ts, job.id),
            )
            conn.execute(
                "UPDATE documents SET status='retrying', stage=NULL, attempts=?, error=?, updated_at=? WHERE id=?",
                (row["attempts"] - 1, error, ts, job.document_id),
            )
            log_event(conn, job.document_id, f"Rate limited by the model provider, waiting {delay:.0f}s (attempt not counted)", "warn")
            return "queued"
        if transient and row["attempts"] < row["max_attempts"]:
            delay = max(retry_after or 0, backoff_seconds(row["attempts"], retry_base_s, retry_max_s))
            conn.execute(
                "UPDATE jobs SET status='queued', run_after=?, lease_until=NULL, worker_id=NULL, last_error=?, updated_at=? WHERE id=?",
                (ts + delay, error, ts, job.id),
            )
            conn.execute(
                "UPDATE documents SET status='retrying', stage=NULL, error=?, updated_at=? WHERE id=?",
                (error, ts, job.document_id),
            )
            log_event(conn, job.document_id, f"Temporary failure, retrying in {delay:.0f}s: {error}", "warn")
            return "queued"
        reason = error if not transient else f"Gave up after {row['attempts']} attempts: {error}"
        _finish_failed(conn, job.id, job.document_id, reason, ts)
        return "failed"


def _finish_failed(conn: sqlite3.Connection, job_id: str, document_id: str, error: str, ts: float) -> None:
    conn.execute(
        "UPDATE jobs SET status='failed', lease_until=NULL, last_error=?, updated_at=? WHERE id=?",
        (error, ts, job_id),
    )
    conn.execute(
        "UPDATE documents SET status='failed', stage=NULL, error=?, updated_at=? WHERE id=?",
        (error, ts, document_id),
    )
    log_event(conn, document_id, f"Processing failed: {error}", "error")


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
