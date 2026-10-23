from __future__ import annotations

import threading

from app import jobqueue
from app.db import connect

from .conftest import add_document


def job_row(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()


def doc_row(conn, doc_id):
    return conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()


def test_only_one_live_job_per_document(conn):
    doc = add_document(conn)
    first = jobqueue.enqueue(conn, doc, 3)
    assert first is not None
    assert jobqueue.enqueue(conn, doc, 3) is None  # duplicate request is ignored
    job = jobqueue.claim(conn, "w1", 30)
    assert jobqueue.enqueue(conn, doc, 3) is None  # still running
    assert jobqueue.complete(conn, job)
    assert jobqueue.enqueue(conn, doc, 3) is not None  # finished jobs allow a new one


def test_claim_marks_document_processing_and_counts_attempt(conn):
    doc = add_document(conn)
    jobqueue.enqueue(conn, doc, 3)
    job = jobqueue.claim(conn, "w1", 30)
    assert job.document_id == doc and job.attempts == 1
    assert doc_row(conn, doc)["status"] == "processing"
    assert jobqueue.claim(conn, "w2", 30) is None  # leased jobs are not handed out twice


def test_transient_failure_is_retried_with_backoff(conn):
    doc = add_document(conn)
    jid = jobqueue.enqueue(conn, doc, 3)
    job = jobqueue.claim(conn, "w1", 30)
    assert jobqueue.fail(conn, job, "429 rate limited", transient=True, retry_base_s=60, retry_max_s=600) == "queued"
    row = job_row(conn, jid)
    assert row["status"] == "queued" and row["worker_id"] is None
    assert doc_row(conn, doc)["status"] == "retrying"
    assert jobqueue.claim(conn, "w1", 30) is None  # not due yet: backoff respected
    conn.execute("UPDATE jobs SET run_after=0 WHERE id=?", (jid,))
    again = jobqueue.claim(conn, "w1", 30)
    assert again.attempts == 2


def test_transient_failures_stop_after_max_attempts(conn):
    doc = add_document(conn)
    jid = jobqueue.enqueue(conn, doc, 2)
    for expected in ("queued", "failed"):
        conn.execute("UPDATE jobs SET run_after=0 WHERE id=?", (jid,))
        job = jobqueue.claim(conn, "w1", 30)
        assert jobqueue.fail(conn, job, "timeout", True, 0, 0) == expected
    d = doc_row(conn, doc)
    assert d["status"] == "failed"
    assert "Gave up after 2 attempts" in d["error"]


def test_permanent_failure_is_not_retried(conn):
    doc = add_document(conn)
    jid = jobqueue.enqueue(conn, doc, 5)
    job = jobqueue.claim(conn, "w1", 30)
    assert jobqueue.fail(conn, job, "Cannot open PDF", False, 0, 0) == "failed"
    assert job_row(conn, jid)["status"] == "failed"
    assert doc_row(conn, doc)["error"] == "Cannot open PDF"


def test_expired_lease_is_reclaimed_and_old_worker_cannot_finish(conn):
    doc = add_document(conn)
    jid = jobqueue.enqueue(conn, doc, 3)
    stale = jobqueue.claim(conn, "dead-worker", 30)
    conn.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (jid,))  # the worker died
    fresh = jobqueue.claim(conn, "w2", 30)
    assert fresh is not None and fresh.attempts == 2
    # The original worker comes back to life: none of its writes may land.
    assert not jobqueue.heartbeat(conn, stale, 30)
    assert not jobqueue.complete(conn, stale)
    assert jobqueue.fail(conn, stale, "boom", False, 0, 0) == "lost"
    assert job_row(conn, jid)["worker_id"] == "w2"
    assert jobqueue.complete(conn, fresh)


def test_repeatedly_crashing_job_ends_failed(conn):
    doc = add_document(conn)
    jid = jobqueue.enqueue(conn, doc, 2)
    for _ in range(2):
        jobqueue.claim(conn, "w", 30)
        conn.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (jid,))
    assert jobqueue.claim(conn, "w", 30) is None
    assert job_row(conn, jid)["status"] == "failed"
    assert doc_row(conn, doc)["status"] == "failed"


def test_concurrent_claims_never_hand_out_a_job_twice(settings, conn):
    docs = [add_document(conn, f"{i}.pdf") for i in range(30)]
    for d in docs:
        jobqueue.enqueue(conn, d, 3)
    claimed: list[str] = []
    lock = threading.Lock()

    def grab(wid: str):
        c = connect(settings.db_path)
        try:
            while (job := jobqueue.claim(c, wid, 30)) is not None:
                with lock:
                    claimed.append(job.id)
        finally:
            c.close()

    threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(claimed) == 30
    assert len(set(claimed)) == 30


def test_rate_limit_defers_without_using_an_attempt_and_honours_hint(conn):
    doc = add_document(conn)
    jid = jobqueue.enqueue(conn, doc, 2)
    job = jobqueue.claim(conn, "w1", 30)
    before = jobqueue.now()
    status = jobqueue.fail(conn, job, "429 quota", True, 0.01, 0.02, retry_after=45, rate_limited=True, max_deferrals=2)
    assert status == "queued"
    row = job_row(conn, jid)
    assert row["attempts"] == 0 and row["deferrals"] == 1
    assert row["run_after"] >= before + 45  # the provider's hint wins over our short backoff
    assert doc_row(conn, doc)["status"] == "retrying"
    # Once deferrals run out, rate limits count as ordinary transient failures again.
    for _ in range(2):
        conn.execute("UPDATE jobs SET run_after=0 WHERE id=?", (jid,))
        job = jobqueue.claim(conn, "w1", 30)
        jobqueue.fail(conn, job, "429 quota", True, 0, 0, rate_limited=True, max_deferrals=2)
    assert job_row(conn, jid)["deferrals"] == 2 and job_row(conn, jid)["attempts"] == 1
    conn.execute("UPDATE jobs SET run_after=0 WHERE id=?", (jid,))
    job = jobqueue.claim(conn, "w1", 30)
    assert jobqueue.fail(conn, job, "429 quota", True, 0, 0, rate_limited=True, max_deferrals=2) == "failed"


def test_retry_hint_and_rate_limiter():
    import time

    from app.llm.gemini import RateLimiter, retry_hint

    assert retry_hint(Exception("Quota exceeded. Please retry in 49.79s.")) == 49.79
    assert retry_hint(Exception("{'retryDelay': '12s'}")) == 12
    assert retry_hint(Exception("503 overloaded")) is None
    assert retry_hint(Exception("Please retry in 20h13.5s.")) == 20 * 3600 + 13.5
    assert retry_hint(Exception("Please retry in 1m30s")) == 90
    lim = RateLimiter(rpm=600)  # 0.1 s apart
    t = time.monotonic()
    for _ in range(3):
        lim.wait()
    assert time.monotonic() - t >= 0.19
