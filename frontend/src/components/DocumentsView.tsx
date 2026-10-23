import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  errorMessage,
  fileUrl,
  fmtTime,
  type DocumentRow,
  type DocumentsResponse,
  type UploadResult,
} from "../api";
import { StatusPill } from "./StatusPill";

const ACTIVE = new Set(["queued", "processing", "retrying"]);
const ACCEPT = ".pdf,.xlsx,.xlsm,.csv";

interface UploadNote {
  key: string;
  filename: string;
  tone: "ok" | "warn" | "bad";
  text: string;
}

function noteFor(r: UploadResult, i: number, fallbackName: string): UploadNote {
  const filename = r.document?.filename ?? r.filename ?? fallbackName;
  const key = `${Date.now()}-${i}`;
  if (r.error) return { key, filename, tone: "bad", text: r.error };
  if (r.duplicate && r.requeued) return { key, filename, tone: "warn", text: "Already uploaded; it had failed, so it was queued again" };
  if (r.duplicate) return { key, filename, tone: "warn", text: `Already uploaded (status: ${r.document?.status ?? "unknown"})` };
  if (r.document?.status === "failed") return { key, filename, tone: "bad", text: r.document.error ?? "Rejected" };
  return { key, filename, tone: "ok", text: "Uploaded and queued for processing" };
}

export function StatusCell({ d }: { d: DocumentRow }) {
  if (d.status === "processing") return <StatusPill status="processing" label={`processing${d.stage ? `: ${d.stage}` : ""}`} />;
  if (d.status === "retrying") return <StatusPill status="retrying" label={`retrying${d.stage ? ` (${d.stage})` : ""}`} />;
  return <StatusPill status={d.status} />;
}

export function useDocuments() {
  const [data, setData] = useState<DocumentsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const live = useRef(true);

  const refresh = useCallback(async () => {
    window.clearTimeout(timer.current);
    let next = 10000;
    try {
      const d = await api.documents();
      if (!live.current) return;
      setData(d);
      setError(null);
      if (d.documents.some((x) => ACTIVE.has(x.status))) next = 1500;
    } catch (e) {
      if (!live.current) return;
      setError(errorMessage(e));
    }
    timer.current = window.setTimeout(refresh, next);
  }, []);

  useEffect(() => {
    live.current = true;
    refresh();
    return () => {
      live.current = false;
      window.clearTimeout(timer.current);
    };
  }, [refresh]);

  return { data, error, refresh };
}

function Uploader({ onDone }: { onDone: () => void }) {
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notes, setNotes] = useState<UploadNote[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const input = useRef<HTMLInputElement | null>(null);

  const send = async (files: File[]) => {
    if (!files.length) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await api.upload(files);
      setNotes((prev) => [...res.map((r, i) => noteFor(r, i, files[i]?.name ?? "file")), ...prev].slice(0, 30));
      onDone();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <section className="card">
      <div
        className={`dropzone${drag ? " drag" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          send(Array.from(e.dataTransfer.files));
        }}
      >
        <div>
          <strong>Drop files here</strong> or{" "}
          <button className="btn btn-primary" disabled={busy} onClick={() => input.current?.click()}>
            Choose files
          </button>
        </div>
        <div className="muted small">PDF bills, policies and records; Excel (.xlsx, .xlsm) or CSV workbooks. Up to 25 MB each.</div>
        {busy && <div className="small">Uploading...</div>}
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          onChange={(e) => send(Array.from(e.target.files ?? []))}
          data-testid="file-input"
        />
      </div>
      {err && <div className="error-box">Upload failed: {err}</div>}
      {notes.length > 0 && (
        <div className="upload-notes">
          <div className="row between">
            <span className="small strong">Upload results</span>
            <button className="btn btn-link small" onClick={() => setNotes([])}>
              Clear
            </button>
          </div>
          <ul>
            {notes.map((n) => (
              <li key={n.key}>
                <StatusPill status={n.tone === "ok" ? "ok" : n.tone === "warn" ? "partial" : "failed"} label={n.tone === "ok" ? "uploaded" : n.tone === "warn" ? "duplicate" : "error"} />{" "}
                <span className="strong">{n.filename}</span> <span className="muted">{n.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function RowActions({ d, onChanged, onOpen }: { d: DocumentRow; onChanged: () => void; onOpen: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="actions">
      {confirm ? (
        <>
          <span className="small">Delete this document?</span>
          <button className="btn btn-s btn-danger" disabled={busy} onClick={() => run(() => api.remove(d.id))}>
            Yes, delete
          </button>
          <button className="btn btn-s" disabled={busy} onClick={() => setConfirm(false)}>
            Cancel
          </button>
        </>
      ) : (
        <>
          <button className="btn btn-s" onClick={onOpen}>
            Open
          </button>
          <button
            className="btn btn-s"
            disabled={busy || d.status === "queued" || d.status === "processing"}
            onClick={() => run(() => api.reprocess(d.id))}
            title="Run extraction again"
          >
            Reprocess
          </button>
          <button className="btn btn-s" disabled={busy} onClick={() => setConfirm(true)}>
            Delete
          </button>
        </>
      )}
      {err && <div className="error-inline small">{err}</div>}
    </div>
  );
}

export function JobsSummary({ jobs }: { jobs: Record<string, number> }) {
  const order = ["queued", "running", "succeeded", "failed"];
  const keys = [...order.filter((k) => k in jobs), ...Object.keys(jobs).filter((k) => !order.includes(k))];
  if (!keys.length) return <span className="muted small">No jobs yet</span>;
  return (
    <div className="row gap-s wrap">
      <span className="small muted">Jobs:</span>
      {keys.map((k) => (
        <span key={k} className="small">
          <StatusPill status={k} /> {jobs[k]}
        </span>
      ))}
    </div>
  );
}

export function DocumentsView({ onOpen }: { onOpen: (id: string) => void }) {
  const { data, error, refresh } = useDocuments();
  const docs = data?.documents ?? [];

  return (
    <div className="stack">
      <Uploader onDone={refresh} />
      <section className="card">
        <div className="card-head">
          <h2>Documents</h2>
          <div className="row gap-m wrap">
            {data && <JobsSummary jobs={data.jobs} />}
            <button className="btn btn-s" onClick={refresh}>
              Refresh
            </button>
          </div>
        </div>
        {error && <div className="error-box">Could not load documents: {error}</div>}
        {!data && !error && <div className="muted pad">Loading...</div>}
        {data && docs.length === 0 && <div className="muted pad">No documents yet. Upload files above.</div>}
        {docs.length > 0 && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>File</th>
                  <th>Type</th>
                  <th>Organisation</th>
                  <th>Status</th>
                  <th className="num">Attempts</th>
                  <th className="num">Facts verified</th>
                  <th className="num">Failed checks</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <tr key={d.id}>
                    <td className="file-cell">
                      <button className="btn-link strong" onClick={() => onOpen(d.id)} title={d.title ?? d.filename}>
                        {d.filename}
                      </button>
                      <div className="muted small">
                        {(d.size_bytes / 1024).toFixed(0)} KB · uploaded {fmtTime(d.created_at)}
                        {" · "}
                        <a href={fileUrl(d.id)} target="_blank" rel="noreferrer">
                          original
                        </a>
                      </div>
                      {d.error && (d.status === "failed" || d.status === "retrying") && (
                        <div className={d.status === "failed" ? "error-inline small" : "warn-inline small"}>{d.error}</div>
                      )}
                    </td>
                    <td>{d.doc_type ? d.doc_type.replace(/_/g, " ") : <span className="muted">-</span>}</td>
                    <td>{d.organisation ?? <span className="muted">-</span>}</td>
                    <td>
                      <StatusCell d={d} />
                    </td>
                    <td className="num">{d.attempts}</td>
                    <td className="num">
                      {d.status === "succeeded" ? `${d.verified_count ?? 0} / ${d.fact_count ?? 0}` : <span className="muted">-</span>}
                    </td>
                    <td className="num">
                      {d.status === "succeeded" ? (
                        (d.failed_checks ?? 0) > 0 ? (
                          <StatusPill status="fail" label={String(d.failed_checks)} />
                        ) : (
                          "0"
                        )
                      ) : (
                        <span className="muted">-</span>
                      )}
                    </td>
                    <td>
                      <RowActions d={d} onChanged={refresh} onOpen={() => onOpen(d.id)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
