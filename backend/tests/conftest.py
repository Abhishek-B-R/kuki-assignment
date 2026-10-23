from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path

import pytest

from app.config import Settings
from app.db import connect, init_db, now

DATASET = Path(__file__).resolve().parents[2] / "dataset"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = dataclasses.replace(
        Settings(),
        data_dir=tmp_path / "data",
        llm_provider="fake",
        worker_threads=2,
        job_max_attempts=3,
        job_lease_s=30,
        retry_base_s=0.01,
        retry_max_s=0.02,
        poll_interval_s=0.02,
        extract_version="v1",
    )
    init_db(s.db_path)
    s.uploads_dir.mkdir(parents=True, exist_ok=True)
    return s


@pytest.fixture
def conn(settings: Settings):
    c = connect(settings.db_path)
    yield c
    c.close()


def add_document(conn, path: Path | str = "x.pdf", filename: str | None = None) -> str:
    doc_id = uuid.uuid4().hex
    ts = now()
    conn.execute(
        "INSERT INTO documents(id, filename, sha256, mime, size_bytes, path, status, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (doc_id, filename or Path(path).name, uuid.uuid4().hex, "application/pdf", 1, str(path), "queued", ts, ts),
    )
    return doc_id
