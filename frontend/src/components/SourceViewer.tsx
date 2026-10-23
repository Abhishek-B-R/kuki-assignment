import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, errorMessage, fileUrl, pageUrl, type DocumentDetailResponse, type SheetResponse } from "../api";
import { StatusPill } from "./StatusPill";

/** A location inside a stored document: a PDF page (with optional highlight boxes) or a sheet range. */
export interface SourceRef {
  documentId: string;
  filename?: string | null;
  page?: number | null;
  sheet?: string | null;
  cellRange?: string | null;
  bbox?: number[][] | null;
  quote?: string | null;
  evidence?: string | null;
  note?: string | null;
  label?: string | null;
}

// Document metadata (kind, page count, sheet names) is shared by every viewer instance.
const metaCache = new Map<string, Promise<DocumentDetailResponse>>();
function loadMeta(id: string): Promise<DocumentDetailResponse> {
  let p = metaCache.get(id);
  if (!p) {
    p = api.document(id);
    p.catch(() => metaCache.delete(id));
    metaCache.set(id, p);
  }
  return p;
}
export function forgetMeta(id: string) {
  metaCache.delete(id);
}

const sheetCache = new Map<string, Promise<SheetResponse>>();
function loadSheet(id: string, sheet: string): Promise<SheetResponse> {
  const key = `${id}\u0000${sheet}`;
  let p = sheetCache.get(key);
  if (!p) {
    p = api.sheet(id, sheet);
    p.catch(() => sheetCache.delete(key));
    sheetCache.set(key, p);
  }
  return p;
}

/** "page 3", "Sheet1!B3:B14", "Sheet1" or "" */
export function locationText(page?: number | null, sheet?: string | null, cellRange?: string | null): string {
  if (page) return `page ${page}`;
  const rng = cellRange ? cellRange.replace(/^.*!/, "") : "";
  if (sheet && rng) return `${sheet}!${rng}`;
  return sheet || rng || "";
}

// ---- cell references ----------------------------------------------------------------------

export function colLetters(n: number): string {
  let s = "";
  while (n > 0) {
    const r = (n - 1) % 26;
    s = String.fromCharCode(65 + r) + s;
    n = Math.floor((n - 1) / 26);
  }
  return s;
}

function colNumber(letters: string): number {
  let n = 0;
  for (const ch of letters.toUpperCase()) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n;
}

interface Box {
  r0: number;
  c0: number;
  r1: number;
  c1: number;
}

/** Parses "B3", "B3:K3", "Sheet!B3:B14", "'My sheet'!$B$3" and comma-separated lists. */
export function parseRanges(range: string | null | undefined): { sheet: string | null; boxes: Box[] } {
  if (!range) return { sheet: null, boxes: [] };
  let sheet: string | null = null;
  const boxes: Box[] = [];
  for (const partRaw of range.split(",")) {
    let part = partRaw.trim();
    const bang = part.lastIndexOf("!");
    if (bang >= 0) {
      let s = part.slice(0, bang).trim();
      if (s.length >= 2 && s.startsWith("'") && s.endsWith("'")) s = s.slice(1, -1).replace(/''/g, "'");
      sheet = sheet ?? s;
      part = part.slice(bang + 1);
    }
    const m = /^\$?([A-Za-z]{1,3})\$?(\d+)(?::\$?([A-Za-z]{1,3})\$?(\d+))?$/.exec(part.trim());
    if (!m) continue;
    const c0 = colNumber(m[1]);
    const r0 = Number(m[2]);
    const c1 = m[3] ? colNumber(m[3]) : c0;
    const r1 = m[4] ? Number(m[4]) : r0;
    boxes.push({ r0: Math.min(r0, r1), r1: Math.max(r0, r1), c0: Math.min(c0, c1), c1: Math.max(c0, c1) });
  }
  return { sheet, boxes };
}

// ---- PDF page -----------------------------------------------------------------------------

function PdfPage({ docId, page, boxes }: { docId: string; page: number; boxes: number[][] }) {
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  useEffect(() => setState("loading"), [docId, page]);
  const firstBox = useRef<HTMLDivElement | null>(null);
  const wrap = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (state !== "ok" || !firstBox.current || !wrap.current) return;
    const scroller = wrap.current.closest(".viewer-body");
    if (scroller instanceof HTMLElement) {
      const top = wrap.current.offsetTop + firstBox.current.offsetTop;
      scroller.scrollTop = Math.max(0, top - scroller.clientHeight / 3);
    }
  }, [state, page, boxes.length, boxes[0]?.[1]]);
  return (
    <div className="pdf-page" ref={wrap}>
      {state === "loading" && <div className="muted pad">Rendering page {page}...</div>}
      {state === "error" && <div className="error-box">Could not render page {page}.</div>}
      <img
        src={pageUrl(docId, page)}
        alt={`Page ${page}`}
        onLoad={() => setState("ok")}
        onError={() => setState("error")}
        style={{ display: state === "error" ? "none" : "block" }}
      />
      {state === "ok" &&
        boxes.map((b, i) => (
          <div
            key={i}
            ref={i === 0 ? firstBox : undefined}
            className="bbox"
            style={{
              left: `${b[0] * 100}%`,
              top: `${b[1] * 100}%`,
              width: `${(b[2] - b[0]) * 100}%`,
              height: `${(b[3] - b[1]) * 100}%`,
            }}
          />
        ))}
    </div>
  );
}

// ---- sheet grid ---------------------------------------------------------------------------

const MAX_ROWS = 1500;
const MAX_COLS = 60;

function SheetGrid({ docId, sheet, boxes }: { docId: string; sheet: string; boxes: Box[] }) {
  const [data, setData] = useState<SheetResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  const target = useRef<HTMLTableCellElement | null>(null);

  useEffect(() => {
    let live = true;
    setData(null);
    setErr(null);
    loadSheet(docId, sheet)
      .then((d) => live && setData(d))
      .catch((e) => live && setErr(errorMessage(e)));
    return () => {
      live = false;
    };
  }, [docId, sheet]);

  const grid = useMemo(() => {
    if (!data) return null;
    const m = new Map<string, { value: string | null; formula: string | null; ref: string }>();
    for (const c of data.cells) m.set(`${c.row}:${c.col}`, c);
    return m;
  }, [data]);

  const first = boxes[0];
  useLayoutEffect(() => {
    const el = target.current;
    const sc = scroller.current;
    if (!el || !sc) return;
    sc.scrollTop = Math.max(0, el.offsetTop - sc.clientHeight / 2);
    sc.scrollLeft = Math.max(0, el.offsetLeft - sc.clientWidth / 2);
  }, [grid, first?.r0, first?.c0]);

  if (err) return <div className="error-box">{err}</div>;
  if (!data || !grid) return <div className="muted pad">Loading sheet...</div>;

  const maxR = Math.max(data.rows, ...boxes.map((b) => b.r1));
  const maxC = Math.max(data.cols, ...boxes.map((b) => b.c1));
  const rows = Math.min(maxR, MAX_ROWS);
  const cols = Math.min(maxC, MAX_COLS);
  const inRange = (r: number, c: number) => boxes.some((b) => r >= b.r0 && r <= b.r1 && c >= b.c0 && c <= b.c1);

  const body = [];
  for (let r = 1; r <= rows; r++) {
    const tds = [];
    for (let c = 1; c <= cols; c++) {
      const cell = grid.get(`${r}:${c}`);
      const hit = inRange(r, c);
      const isTarget = first && r === first.r0 && c === first.c0;
      tds.push(
        <td
          key={c}
          ref={isTarget ? target : undefined}
          className={hit ? "cell-hit" : undefined}
          title={cell ? `${cell.ref}${cell.formula ? `  ${cell.formula}` : ""}` : `${colLetters(c)}${r}`}
        >
          {cell?.value ?? ""}
        </td>,
      );
    }
    body.push(
      <tr key={r}>
        <th className="rownum">{r}</th>
        {tds}
      </tr>,
    );
  }

  return (
    <>
      {(maxR > MAX_ROWS || maxC > MAX_COLS) && (
        <div className="muted small pad-x">
          Showing the first {rows} rows and {cols} columns of {maxR} x {maxC}.
        </div>
      )}
      <div className="sheet-scroll" ref={scroller}>
        <table className="sheet">
          <thead>
            <tr>
              <th className="corner" />
              {Array.from({ length: cols }, (_, i) => (
                <th key={i}>{colLetters(i + 1)}</th>
              ))}
            </tr>
          </thead>
          <tbody>{body}</tbody>
        </table>
      </div>
    </>
  );
}

// ---- viewer -------------------------------------------------------------------------------

export function SourceViewer({ source, onClose }: { source: SourceRef; onClose?: () => void }) {
  const [meta, setMeta] = useState<DocumentDetailResponse | null>(null);
  const [metaErr, setMetaErr] = useState<string | null>(null);
  const parsed = useMemo(() => parseRanges(source.cellRange), [source.cellRange]);
  const citedSheet = source.sheet || parsed.sheet;
  const [page, setPage] = useState<number>(source.page || 1);
  const [sheet, setSheet] = useState<string | null>(citedSheet);

  useEffect(() => {
    setPage(source.page || 1);
    setSheet(source.sheet || parseRanges(source.cellRange).sheet);
  }, [source.documentId, source.page, source.sheet, source.cellRange]);

  useEffect(() => {
    let live = true;
    setMetaErr(null);
    loadMeta(source.documentId)
      .then((m) => {
        if (!live) return;
        setMeta(m);
      })
      .catch((e) => live && setMetaErr(errorMessage(e)));
    return () => {
      live = false;
    };
  }, [source.documentId]);

  const doc = meta?.document;
  const isPdf = doc ? doc.mime === "application/pdf" || doc.filename.toLowerCase().endsWith(".pdf") : !!source.page;
  const sheets = meta?.sheets ?? [];
  const activeSheet = sheet ?? (sheets.length ? sheets[0] : null);
  const pageCount = doc?.page_count ?? null;
  const boxes = isPdf && source.page && page === source.page ? source.bbox ?? [] : [];
  const cellBoxes = activeSheet && activeSheet === citedSheet ? parsed.boxes : [];

  return (
    <div className="viewer">
      <div className="viewer-head">
        <div className="viewer-title">
          <strong title={doc?.filename ?? source.filename ?? ""}>{doc?.filename ?? source.filename ?? "Document"}</strong>
          <span className="muted small">{locationText(source.page, citedSheet, source.cellRange)}</span>
        </div>
        <div className="row gap-s">
          <a className="btn btn-s" href={fileUrl(source.documentId)} target="_blank" rel="noreferrer">
            Original
          </a>
          {onClose && (
            <button className="btn btn-s" onClick={onClose} aria-label="Close source">
              Close
            </button>
          )}
        </div>
      </div>
      {(source.label || source.quote || source.evidence) && (
        <div className="viewer-quote">
          {source.label && <div className="small strong">{source.label}</div>}
          {source.quote && <div className="quote">"{source.quote}"</div>}
          {source.evidence && (
            <div className="row gap-s small">
              <StatusPill status={source.evidence} />
              {source.note && <span className="muted">{source.note}</span>}
            </div>
          )}
        </div>
      )}
      {metaErr && <div className="error-box">{metaErr}</div>}
      {meta && isPdf && (
        <div className="viewer-tools">
          <button className="btn btn-s" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>
            Prev
          </button>
          <span className="small">
            Page {page}
            {pageCount ? ` of ${pageCount}` : ""}
          </span>
          <button
            className="btn btn-s"
            disabled={pageCount !== null && page >= pageCount}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
          </button>
          {source.page && page !== source.page && (
            <button className="btn btn-s" onClick={() => setPage(source.page!)}>
              Back to cited page
            </button>
          )}
          {source.page && page === source.page && boxes.length === 0 && (source.quote || source.label) && (
            <span className="muted small">No highlight position stored for this value.</span>
          )}
        </div>
      )}
      {meta && !isPdf && sheets.length > 0 && (
        <div className="viewer-tools">
          <label className="small">
            Sheet{" "}
            <select value={activeSheet ?? ""} onChange={(e) => setSheet(e.target.value)}>
              {sheets.map((s) => (
                <option key={s} value={s}>
                  {s}
                  {s === citedSheet ? " (cited)" : ""}
                </option>
              ))}
            </select>
          </label>
          {source.cellRange && activeSheet === citedSheet && (
            <span className="muted small">Highlighted: {source.cellRange.replace(/^.*!/, "")}</span>
          )}
        </div>
      )}
      <div className="viewer-body">
        {!meta && !metaErr && <div className="muted pad">Loading document...</div>}
        {meta && isPdf && <PdfPage docId={source.documentId} page={page} boxes={boxes} />}
        {meta && !isPdf && activeSheet && <SheetGrid docId={source.documentId} sheet={activeSheet} boxes={cellBoxes} />}
        {meta && !isPdf && !activeSheet && (
          <div className="muted pad">No parsed cells are stored for this document.</div>
        )}
      </div>
    </div>
  );
}

/** Right-hand panel used by Ask and Financial-year views. */
export function SourceDrawer({ source, onClose }: { source: SourceRef | null; onClose: () => void }) {
  useEffect(() => {
    if (!source) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [source, onClose]);
  if (!source) return null;
  return (
    <div className="drawer" role="dialog" aria-label="Source">
      <SourceViewer source={source} onClose={onClose} />
    </div>
  );
}
