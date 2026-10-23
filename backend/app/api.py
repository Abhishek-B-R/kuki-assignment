"""HTTP API (FastAPI). The dashboard in frontend/ is served from / when it has been built."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import jobqueue, qa, repo
from .config import ROOT, Settings, get_settings
from .db import init_db, log_event, now, session, transaction
from .fy import available_years, fy_report, parse_fy
from .llm import LLM, LLMError, make_llm
from .parsing.pdf import render_page_png
from .pipeline import SUPPORTED

log = logging.getLogger(__name__)

EVAL_RESULTS = ROOT / "backend" / "evals" / "results"
FRONTEND_DIST = ROOT / "frontend" / "dist"
MIME = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".csv": "text/csv",
}


class AskRequest(BaseModel):
    question: str
    document_ids: list[str] | None = None
    financial_year: str | None = None


def _public(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """Never expose server filesystem paths."""
    if doc is not None:
        doc = {k: v for k, v in doc.items() if k != "path"}
    return doc


def create_app(settings: Settings | None = None, llm: LLM | None = None) -> FastAPI:
    settings = settings or get_settings()
    state: dict[str, Any] = {"llm": llm, "worker": None}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db(settings.db_path)
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        if os.environ.get("EMBEDDED_WORKER") == "1":
            from .worker import Worker

            state["worker"] = Worker(settings, llm=state["llm"])
            state["worker"].start()
        yield
        if state["worker"]:
            state["worker"].shutdown()

    app = FastAPI(title="KuKi sustainability data assistant", lifespan=lifespan)

    def db():
        return session(settings.db_path)

    def get_llm() -> LLM:
        if state["llm"] is None:
            try:
                state["llm"] = make_llm(settings)
            except LLMError as exc:
                raise HTTPException(503, str(exc)) from exc
        return state["llm"]

    # ---- documents --------------------------------------------------------------------

    @app.post("/api/documents")
    async def upload(files: list[UploadFile] = File(...)) -> list[dict[str, Any]]:
        results = []
        for up in files:
            results.append(await _store_upload(up))
        return results

    async def _store_upload(up: UploadFile) -> dict[str, Any]:
        filename = Path(up.filename or "upload").name
        ext = Path(filename).suffix.lower()
        limit = settings.max_upload_mb * 1024 * 1024
        digest = hashlib.sha256()
        size = 0
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=settings.uploads_dir, suffix=".part")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := await up.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        return {"filename": filename, "error": f"File is larger than {settings.max_upload_mb} MB", "duplicate": False}
                    digest.update(chunk)
                    out.write(chunk)
            if size == 0:
                return {"filename": filename, "error": "File is empty", "duplicate": False}
            sha = digest.hexdigest()
            final = settings.uploads_dir / f"{sha}{ext}"
            with db() as conn, transaction(conn):
                existing = conn.execute("SELECT id, status FROM documents WHERE sha256=?", (sha,)).fetchone()
                if existing:
                    requeued = False
                    if existing["status"] == "failed" and ext in SUPPORTED:
                        # Uploading a failed file again is a retry request.
                        requeued = _requeue(conn, existing["id"], "Re-uploaded after failure")
                    return {"document": _public(repo.get_document(conn, existing["id"])), "duplicate": True, "requeued": requeued}
                if not final.exists():
                    tmp.replace(final)
                doc_id = uuid.uuid4().hex
                ts = now()
                supported = ext in SUPPORTED
                conn.execute(
                    "INSERT INTO documents(id, filename, sha256, mime, size_bytes, path, status, error, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        doc_id, filename, sha, MIME.get(ext, up.content_type or "application/octet-stream"), size,
                        str(final), "queued" if supported else "failed",
                        None if supported else f"Unsupported file type '{ext or '(none)'}'. Supported: {', '.join(sorted(SUPPORTED))}",
                        ts, ts,
                    ),
                )
                log_event(conn, doc_id, f"Uploaded ({size:,} bytes)")
                if supported:
                    jobqueue.enqueue(conn, doc_id, settings.job_max_attempts)
                    log_event(conn, doc_id, "Queued for processing")
                else:
                    log_event(conn, doc_id, "Rejected: unsupported file type", "error")
                return {"document": _public(repo.get_document(conn, doc_id)), "duplicate": False}
        finally:
            tmp.unlink(missing_ok=True)

    def _requeue(conn, doc_id: str, reason: str) -> bool:
        if jobqueue.enqueue(conn, doc_id, settings.job_max_attempts) is None:
            return False
        conn.execute(
            "UPDATE documents SET status='queued', stage=NULL, error=NULL, attempts=0, updated_at=? WHERE id=?",
            (now(), doc_id),
        )
        log_event(conn, doc_id, reason)
        return True

    @app.get("/api/documents")
    def documents() -> dict[str, Any]:
        with db() as conn:
            return {"documents": repo.list_documents(conn), "jobs": jobqueue.counts(conn)}

    @app.get("/api/documents/{doc_id}")
    def document(doc_id: str) -> dict[str, Any]:
        with db() as conn:
            doc = repo.get_document(conn, doc_id)
            if not doc:
                raise HTTPException(404, "Document not found")
            doc = _public(doc)
            facts = [f for f in repo.facts_for(conn, [doc_id])] if doc["status"] == "succeeded" else []
            sheets = sorted({c.sheet for c in repo.cells_for(conn, doc_id)})
            return {
                "document": doc,
                "facts": facts,
                "checks": repo.checks_for(conn, [doc_id]),
                "events": repo.events_for(conn, doc_id),
                "sheets": sheets,
            }

    @app.post("/api/documents/{doc_id}/reprocess")
    def reprocess(doc_id: str) -> dict[str, Any]:
        with db() as conn, transaction(conn):
            doc = repo.get_document(conn, doc_id)
            if not doc:
                raise HTTPException(404, "Document not found")
            if Path(doc["path"]).suffix.lower() not in SUPPORTED:
                raise HTTPException(400, "This file type cannot be processed")
            if not _requeue(conn, doc_id, "Reprocessing requested"):
                raise HTTPException(409, "Document is already queued or processing")
            return {"document": _public(repo.get_document(conn, doc_id))}

    @app.delete("/api/documents/{doc_id}")
    def delete(doc_id: str) -> dict[str, Any]:
        with db() as conn, transaction(conn):
            doc = repo.get_document(conn, doc_id)
            if not doc:
                raise HTTPException(404, "Document not found")
            conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        Path(doc["path"]).unlink(missing_ok=True)
        return {"deleted": doc_id}

    @app.get("/api/documents/{doc_id}/file")
    def original(doc_id: str):
        with db() as conn:
            doc = repo.get_document(conn, doc_id)
        if not doc or not Path(doc["path"]).exists():
            raise HTTPException(404, "File not found")
        return FileResponse(doc["path"], media_type=doc["mime"], filename=doc["filename"])

    @app.get("/api/documents/{doc_id}/pages/{page_no}.png")
    def page_image(doc_id: str, page_no: int):
        with db() as conn:
            doc = repo.get_document(conn, doc_id)
        if not doc or not doc["path"].lower().endswith(".pdf"):
            raise HTTPException(404, "Not a PDF")
        try:
            png = render_page_png(Path(doc["path"]), page_no)
        except IndexError as exc:
            raise HTTPException(404, "No such page") from exc
        return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/documents/{doc_id}/sheets/{sheet}")
    def sheet_cells(doc_id: str, sheet: str) -> dict[str, Any]:
        with db() as conn:
            cells = [c for c in repo.cells_for(conn, doc_id) if c.sheet == sheet]
        if not cells:
            raise HTTPException(404, "No such sheet")
        return {
            "sheet": sheet,
            "rows": max(c.row for c in cells),
            "cols": max(c.col for c in cells),
            "cells": [{"ref": c.ref, "row": c.row, "col": c.col, "value": c.value, "formula": c.formula} for c in cells],
        }

    # ---- questions & financial years --------------------------------------------------

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        if not req.question.strip():
            raise HTTPException(400, "Question is empty")
        llm = get_llm()
        with db() as conn:
            try:
                return qa.ask(conn, llm, req.question.strip(), req.document_ids, req.financial_year)
            except LLMError as exc:
                raise HTTPException(503 if exc.transient else 502, f"Model call failed: {exc}") from exc

    @app.get("/api/fy")
    def fy_years(document_ids: list[str] | None = Query(None)) -> dict[str, Any]:
        with db() as conn:
            ready, _ = repo.ready_ids(conn, document_ids)
            return {"years": available_years(repo.facts_for(conn, ready))}

    @app.get("/api/fy/{label}")
    def fy(label: str, document_ids: list[str] | None = Query(None)) -> dict[str, Any]:
        fy_ = parse_fy(label if label.upper().startswith("FY") else f"FY {label}")
        if fy_ is None:
            raise HTTPException(400, "Use a financial year like FY2024-25")
        with db() as conn:
            ready, pending = repo.ready_ids(conn, document_ids)
            rep = fy_report(repo.facts_for(conn, ready), fy_)
            rep["not_ready"] = pending
            return rep

    # ---- evaluation results -----------------------------------------------------------

    @app.get("/api/evals")
    def evals() -> dict[str, Any]:
        out: dict[str, Any] = {"runs": {}}
        for name in ("baseline", "improved"):
            p = EVAL_RESULTS / f"{name}.json"
            if p.exists():
                out["runs"][name] = json.loads(p.read_text())
            # Answer-only repeats on the same extraction, summarised to show run-to-run noise.
            out.setdefault("repeats", {})[name] = [
                {"name": r["name"], "ran_at": r["ran_at"], "summary": r["summary"]}
                for r in (json.loads(x.read_text()) for x in sorted(EVAL_RESULTS.glob(f"{name}-r*.json")))
            ]
        cases = ROOT / "backend" / "evals" / "cases.yaml"
        if cases.exists():
            import yaml

            out["cases"] = yaml.safe_load(cases.read_text())
        return out

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        with db() as conn:
            return {"ok": True, "jobs": jobqueue.counts(conn), "model": settings.model_name, "extract_version": settings.extract_version}

    @app.exception_handler(Exception)
    async def unhandled(_req, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"detail": f"Internal error: {type(exc).__name__}"}, status_code=500)

    if FRONTEND_DIST.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="dashboard")

    return app

