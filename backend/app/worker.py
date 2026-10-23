"""Background worker. Run with `python -m app.worker`; any number of processes can run at once.

Each thread loops: claim a job, keep its lease alive from a heartbeat thread while the pipeline
runs, then record the outcome. A crash (even kill -9) just stops the heartbeat; the lease
expires and another worker picks the job up again.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import uuid
from collections.abc import Callable

from . import jobqueue
from .config import Settings, get_settings
from .db import connect, init_db
from .llm import LLM, LLMError, make_llm
from .pipeline import LeaseLost, PermanentError, process_document

log = logging.getLogger("worker")


class Worker:
    def __init__(self, settings: Settings, llm: LLM | None = None, llm_factory: Callable[[], LLM] | None = None):
        self.settings = settings
        self._llm = llm
        self._llm_factory = llm_factory or (lambda: make_llm(settings))
        self.stop = threading.Event()
        self.threads: list[threading.Thread] = []
        self.base_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = self._llm_factory()
        return self._llm

    def db(self):
        return connect(self.settings.db_path)

    def start(self) -> None:
        init_db(self.settings.db_path)
        for i in range(self.settings.worker_threads):
            t = threading.Thread(target=self._loop, args=(f"{self.base_id}/{i}",), name=f"worker-{i}", daemon=True)
            t.start()
            self.threads.append(t)

    def shutdown(self, timeout: float = 30) -> None:
        self.stop.set()
        for t in self.threads:
            t.join(timeout)

    def _loop(self, worker_id: str) -> None:
        conn = self.db()
        try:
            while not self.stop.is_set():
                try:
                    job = jobqueue.claim(conn, worker_id, self.settings.job_lease_s)
                except Exception:
                    log.exception("claim failed")
                    job = None
                if job is None:
                    self.stop.wait(self.settings.poll_interval_s)
                    continue
                self.run_job(job, conn)
        finally:
            conn.close()

    def run_job(self, job: jobqueue.Job, conn) -> None:
        log.info("job %s doc %s attempt %d", job.id, job.document_id, job.attempts)
        done = threading.Event()
        hb = threading.Thread(target=self._heartbeat, args=(job, done), daemon=True)
        hb.start()
        s = self.settings
        try:
            process_document(self.db, self.llm, job, s.job_lease_s, s.extract_version)
        except LeaseLost:
            log.warning("job %s: lease lost, discarding this attempt", job.id)
        except PermanentError as exc:
            jobqueue.fail(conn, job, str(exc), False, s.retry_base_s, s.retry_max_s)
        except LLMError as exc:
            jobqueue.fail(
                conn, job, str(exc), exc.transient, s.retry_base_s, s.retry_max_s,
                retry_after=getattr(exc, "retry_after", None),
                rate_limited=getattr(exc, "rate_limited", False),
                max_deferrals=s.job_max_deferrals,
            )
        except Exception as exc:  # unknown: retry within the attempt budget, then fail visibly
            log.exception("job %s crashed", job.id)
            jobqueue.fail(conn, job, f"Unexpected error: {type(exc).__name__}: {exc}", True, s.retry_base_s, s.retry_max_s)
        finally:
            done.set()
            hb.join(5)

    def _heartbeat(self, job: jobqueue.Job, done: threading.Event) -> None:
        conn = self.db()
        try:
            interval = max(1.0, self.settings.job_lease_s / 3)
            while not done.wait(interval):
                if not jobqueue.heartbeat(conn, job, self.settings.job_lease_s):
                    return
        finally:
            conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(levelname)s %(message)s")
    settings = get_settings()
    worker = Worker(settings)
    worker.start()
    log.info("worker %s started with %d threads (db %s)", worker.base_id, settings.worker_threads, settings.db_path)

    def _stop(signum, _frame):
        log.info("signal %s: finishing current jobs", signum)
        worker.stop.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while not worker.stop.is_set():
        worker.stop.wait(1)
    worker.shutdown()


if __name__ == "__main__":
    main()
