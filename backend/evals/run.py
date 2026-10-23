"""Run the evaluation end to end: ingest dataset/ through the real upload API and worker into a
fresh database, ask every case's question, score, and save results.

    uv run python -m evals.run --name baseline --version v1
    uv run python -m evals.run --name improved --version v2 --reuse-db baseline \
        --reextract "Annual Electricity Consumption.xlsx"   # v2 only changes spreadsheet extraction
    uv run python -m evals.run --name baseline --version v1 --resume              # continue after a quota stop
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app import jobqueue, qa, repo
from app.api import create_app
from app.config import ROOT, get_settings
from app.db import connect, session
from app.llm import LLMError, make_llm
from app.pipeline import EXTRACT_VERSIONS
from app.worker import Worker

from .score import score_case, summarise

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
WORK = HERE / ".work"
DATASET = ROOT / "dataset"


class QuotaExhausted(Exception):
    pass


LONG_WAIT_S = 600


def ingest(settings, llm) -> None:
    """Upload dataset/ and wait for processing. Safe to call again on the same data dir: duplicates are
    skipped, failed documents are requeued, and parked jobs are made due again."""
    client = TestClient(create_app(settings, llm=llm))
    with client:
        with session(settings.db_path) as conn:
            conn.execute("UPDATE jobs SET run_after=? WHERE status='queued'", (time.time(),))
        worker = Worker(settings, llm=llm)
        worker.start()
        try:
            files = [("files", (p.name, p.read_bytes())) for p in sorted(DATASET.iterdir()) if not p.name.startswith(".")]
            client.post("/api/documents", files=files).raise_for_status()
            deadline = time.time() + 3600
            while time.time() < deadline:
                docs = client.get("/api/documents").json()["documents"]
                states = {d["filename"]: d["status"] for d in docs}
                print("  " + "  ".join(f"{k[:18]}={v}" for k, v in states.items()), end="\r", flush=True)
                if all(s in ("succeeded", "failed") for s in states.values()):
                    break
                with session(settings.db_path) as conn:
                    live = conn.execute("SELECT run_after FROM jobs WHERE status IN ('queued','running')").fetchall()
                    running = conn.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone()
                if live and not running and all(r["run_after"] > time.time() + LONG_WAIT_S for r in live):
                    raise QuotaExhausted("every remaining job is waiting on a long provider rate limit")
                time.sleep(2)
            print()
        finally:
            worker.shutdown()


def _rebase_paths(settings, src: Path, dst: Path) -> None:
    """Point copied documents at the copied upload files."""
    with session(settings.db_path) as conn:
        for row in conn.execute("SELECT id, path FROM documents").fetchall():
            p = Path(row["path"])
            if src in p.parents:
                conn.execute("UPDATE documents SET path=? WHERE id=?", (str(dst / p.relative_to(src)), row["id"]))


def extraction_snapshot(conn) -> list[dict]:
    out = []
    for d in repo.list_documents(conn):
        out.append(
            {
                "filename": d["filename"],
                "status": d["status"],
                "error": d["error"],
                "attempts": d["attempts"],
                "doc_type": d["doc_type"],
                "pipeline_version": d["pipeline_version"],
                "organisation": d["organisation"],
                "facts": d["fact_count"],
                "verified_facts": d["verified_count"],
                "failed_checks": d["failed_checks"],
            }
        )
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="results file name, e.g. baseline / improved")
    ap.add_argument("--version", default="v1", choices=EXTRACT_VERSIONS, help="extraction pipeline version")
    ap.add_argument("--reuse-db", help="start from the processed database of an earlier run")
    ap.add_argument("--reextract", nargs="*", default=[], help="with --reuse-db: files to extract again with --version")
    ap.add_argument("--only", nargs="*", help="case ids to run")
    ap.add_argument("--resume", action="store_true", help="keep processed documents and answered cases from an interrupted run")
    ap.add_argument("--answers-only", action="store_true",
                    help="re-answer every case on this run's existing documents (no ingest, no extraction)")
    args = ap.parse_args(argv)

    work = WORK / args.name
    partial = RESULTS / f"{args.name}.partial.json"
    previous = RESULTS / f"{args.name}.json"
    if args.answers_only:
        if not (work / "kuki.db").exists() or not previous.exists():
            raise SystemExit(f"--answers-only: no finished run named {args.name!r} to re-answer")
        prev = json.loads(previous.read_text())
        args.version, args.reuse_db, args.reextract = prev["version"], prev["reused_extraction_from"], prev["reextracted"]
    if work.exists() and not args.resume and not args.answers_only:
        shutil.rmtree(work)
        partial.unlink(missing_ok=True)
    fresh = not work.exists()
    work.mkdir(parents=True, exist_ok=True)
    settings = dataclasses.replace(get_settings(), data_dir=work, retry_base_s=10, retry_max_s=90, extract_version=args.version)
    llm = make_llm(settings)

    try:
        if args.answers_only:
            print(f"Re-answering on the documents already in {work}")
        elif args.reuse_db:
            if fresh:
                src = WORK / args.reuse_db
                shutil.copytree(src, work, dirs_exist_ok=True)
                _rebase_paths(settings, src, work)
                print(f"Reusing processed documents from {src}")
                with session(settings.db_path) as conn:
                    for name in args.reextract:
                        row = conn.execute("SELECT id FROM documents WHERE filename=?", (name,)).fetchone()
                        if row is None:
                            raise SystemExit(f"--reextract: no document named {name!r}")
                        jobqueue.enqueue(conn, row["id"], settings.job_max_attempts)
                        conn.execute("UPDATE documents SET status='queued', stage=NULL WHERE id=?", (row["id"],))
                        print(f"Re-extracting {name} with {args.version}")
            if args.reextract:
                ingest(settings, llm)
        else:
            print(f"Ingesting {DATASET} with {llm.name} ...")
            ingest(settings, llm)
    except QuotaExhausted as exc:
        print(f"\nStopped: {exc}. Rerun with --resume after the quota resets.")
        return 2

    cases = yaml.safe_load((HERE / "cases.yaml").read_text())
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    done = {r["id"]: r for r in json.loads(partial.read_text())} if (args.resume and partial.exists()) else {}
    conn = connect(settings.db_path)
    by_name = {d["filename"]: d["id"] for d in repo.list_documents(conn)}
    results = []
    RESULTS.mkdir(exist_ok=True)
    for case in cases:
        prev = done.get(case["id"])
        if prev and prev["answer"].get("status") != "error":
            results.append(prev)
            continue
        doc_ids = None if case["documents"] == "all" else [by_name[n] for n in case["documents"] if n in by_name]
        print(f"{case['id']} {case['title']} ...", flush=True)
        t0 = time.time()
        ans = None
        for attempt in range(8):
            try:
                ans = qa.ask(conn, llm, case["question"], doc_ids)
                break
            except LLMError as exc:
                hint = getattr(exc, "retry_after", None) or 0
                if hint > LONG_WAIT_S:
                    partial.write_text(json.dumps(results + [r for r in done.values() if r["id"] not in {x["id"] for x in results}], indent=1, default=str))
                    print(f"\nStopped: provider asks to wait {hint / 3600:.1f} h (daily quota). "
                          f"{len(results)} cases saved; rerun with --resume after the reset.")
                    return 2
                if not exc.transient or attempt == 7:
                    ans = {"status": "error", "answer": f"Model call failed: {exc}", "citations": [], "calculations": [],
                           "key_values": [], "gaps": [], "inconsistencies": [], "assumptions": []}
                    break
                wait = max(hint, 10 * (attempt + 1)) + 1
                print(f"   transient ({str(exc)[:60]}...), retrying in {wait:.0f}s", flush=True)
                time.sleep(wait)
        score = score_case(case, ans)
        mark = "PASS" if score["passed"] else "FAIL"
        print(f"   {mark} overall={score['overall']:.2f} {score['scores']} ({time.time() - t0:.1f}s)")
        results.append({"id": case["id"], "title": case["title"], "question": case["question"], "answer": ans, "score": score})
        partial.write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str))

    out = {
        "name": args.name,
        "version": args.version,
        "model": llm.name,
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reused_extraction_from": args.reuse_db,
        "reextracted": args.reextract,
        "extraction": extraction_snapshot(conn),
        "summary": summarise(results),
        "cases": results,
    }
    conn.close()
    path = RESULTS / f"{args.name}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    partial.unlink(missing_ok=True)
    s = out["summary"]
    print(f"\n{args.name}: {s['passed']}/{s['cases']} passed, overall {s['overall']:.3f} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
