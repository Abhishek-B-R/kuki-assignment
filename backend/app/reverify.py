"""Recompute evidence checks for processed documents without calling the model.

    uv run python -m app.reverify [--data-dir DIR]

Re-parses each stored file, re-verifies every stored fact against the page text / cells, and,
when the raw extraction is stored, recomputes the arithmetic checks. Use it after changing
verification or check logic, so existing results benefit without spending model quota.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import uuid
from collections import Counter
from pathlib import Path

from .config import get_settings
from .db import init_db, log_event, session, transaction
from .pipeline import parse, run_checks, verify_facts
from .schemas import ExtractedFact, Extraction


def _fact_from_row(r) -> ExtractedFact:
    return ExtractedFact(
        id=r["local_id"] or r["id"], key=r["key"], label=r["label"] or r["key"], category=r["category"],
        metric=r["metric"], dimension=r["dimension"], primary=bool(r["is_primary"]), value_text=r["value_text"] or "",
        value_num=r["value_num"], unit=r["unit"], period_start=r["period_start"], period_end=r["period_end"],
        page=r["page"], sheet=r["sheet"], cell_range=r["cell_range"], quote=r["quote"], notes=None,
    )


def reverify(db_path: Path) -> dict[str, Counter]:
    init_db(db_path)
    out: dict[str, Counter] = {}
    with session(db_path) as conn:
        docs = conn.execute("SELECT id, filename, path, extraction_json FROM documents WHERE status='succeeded'").fetchall()
        for d in docs:
            parsed = parse(Path(d["path"]))
            rows = conn.execute("SELECT * FROM facts WHERE document_id=? ORDER BY rowid", (d["id"],)).fetchall()
            verified = verify_facts(Path(d["path"]), parsed, [_fact_from_row(r) for r in rows])
            with transaction(conn):
                conn.execute("DELETE FROM pages WHERE document_id=?", (d["id"],))
                conn.executemany(
                    "INSERT INTO pages(document_id, page_no, text, reading_text, word_count, width, height) VALUES (?,?,?,?,?,?,?)",
                    [(d["id"], p.page_no, p.text, p.reading_text, p.word_count, p.width, p.height) for p in parsed.pages],
                )
                for r, v in zip(rows, verified):
                    conn.execute(
                        "UPDATE facts SET evidence_status=?, page=?, sheet=?, cell_range=?, bbox_json=? WHERE id=?",
                        (v.status, v.page if v.page is not None else r["page"], v.sheet or r["sheet"],
                         v.cell_range or r["cell_range"], json.dumps(v.bbox) if v.bbox else None, r["id"]),
                    )
                if d["extraction_json"]:
                    checks = run_checks(parsed, Extraction.model_validate_json(d["extraction_json"]))
                    conn.execute("DELETE FROM checks WHERE document_id=?", (d["id"],))
                    conn.executemany(
                        "INSERT INTO checks(id, document_id, name, status, expected, actual, difference, source, details_json)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        [(uuid.uuid4().hex, d["id"], c.name, c.status, c.expected, c.actual, c.difference, c.source,
                          json.dumps(c.details)) for c in checks],
                    )
                log_event(conn, d["id"], "Evidence re-verified offline")
            out[d["filename"]] = Counter(v.status for v in verified)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path)
    args = ap.parse_args()
    settings = get_settings()
    if args.data_dir:
        settings = dataclasses.replace(settings, data_dir=args.data_dir.resolve())
    for name, counts in reverify(settings.db_path).items():
        print(f"{name}: {dict(counts)}")


if __name__ == "__main__":
    main()
