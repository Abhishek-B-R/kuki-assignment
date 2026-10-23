import { useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  errorMessage,
  fileUrl,
  fmtNum,
  fmtPeriod,
  fmtTime,
  type DocumentDetailResponse,
  type Fact,
} from "../api";
import { StatusCell } from "./DocumentsView";
import { forgetMeta, locationText, SourceViewer, type SourceRef } from "./SourceViewer";
import { StatusPill, Tag } from "./StatusPill";

const ACTIVE = new Set(["queued", "processing", "retrying"]);

const CATEGORY_ORDER = [
  "period",
  "consumption",
  "generation",
  "meter_reading",
  "quantity",
  "demand",
  "financial",
  "rate",
  "date",
  "identifier",
  "party",
  "policy_scope",
  "policy_commitment",
  "other",
];

function factSource(f: Fact): SourceRef {
  return {
    documentId: f.document_id,
    filename: f.filename,
    page: f.page,
    sheet: f.sheet,
    cellRange: f.cell_range,
    bbox: f.bbox,
    quote: f.quote,
    evidence: f.evidence_status,
    note: f.notes,
    label: `${f.label ?? f.key}: ${f.value_text ?? ""}${f.unit ? ` ${f.unit}` : ""}`,
  };
}

export function DocumentDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const [data, setData] = useState<DocumentDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [source, setSource] = useState<SourceRef | null>(null);
  const lastStatus = useRef<string | null>(null);

  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const load = async () => {
      let next = 0;
      try {
        const d = await api.document(id);
        if (!live) return;
        if (lastStatus.current && lastStatus.current !== d.document.status) forgetMeta(id);
        lastStatus.current = d.document.status;
        setData(d);
        setError(null);
        if (ACTIVE.has(d.document.status)) next = 1500;
      } catch (e) {
        if (!live) return;
        setError(errorMessage(e));
      }
      if (next && live) timer = window.setTimeout(load, next);
    };
    setData(null);
    setSelected(null);
    setSource(null);
    lastStatus.current = null;
    load();
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [id]);

  const groups = useMemo(() => {
    const m = new Map<string, Fact[]>();
    for (const f of data?.facts ?? []) {
      const k = f.category || "other";
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(f);
    }
    const rank = (c: string) => {
      const i = CATEGORY_ORDER.indexOf(c);
      return i < 0 ? 99 : i;
    };
    return [...m.entries()].sort((a, b) => rank(a[0]) - rank(b[0]) || a[0].localeCompare(b[0]));
  }, [data]);

  if (error && !data) {
    return (
      <div className="stack">
        <button className="btn btn-s" onClick={onBack}>
          Back to documents
        </button>
        <div className="error-box">{error}</div>
      </div>
    );
  }
  if (!data) return <div className="muted pad">Loading document...</div>;

  const d = data.document;
  const isPdf = d.mime === "application/pdf" || d.filename.toLowerCase().endsWith(".pdf");
  const defaultSource: SourceRef | null =
    d.status === "succeeded" && (isPdf || data.sheets.length > 0)
      ? { documentId: d.id, filename: d.filename, page: isPdf ? 1 : null, sheet: isPdf ? null : data.sheets[0] }
      : null;
  const shown = source ?? defaultSource;
  const verified = data.facts.filter((f) => f.evidence_status === "verified").length;

  return (
    <div className="stack">
      <div className="row between wrap gap-s">
        <button className="btn btn-s" onClick={onBack}>
          Back to documents
        </button>
        <a className="btn btn-s" href={fileUrl(d.id)} target="_blank" rel="noreferrer">
          Open original file
        </a>
      </div>

      <section className="card">
        <div className="card-head">
          <div>
            <h2>{d.title || d.filename}</h2>
            <div className="muted small">{d.filename}</div>
          </div>
          <StatusCell d={d} />
        </div>
        {d.error && <div className={d.status === "failed" ? "error-box" : "warn-box"}>{d.error}</div>}
        <dl className="meta-grid">
          <div>
            <dt>Type</dt>
            <dd>{d.doc_type ? d.doc_type.replace(/_/g, " ") : "-"}</dd>
          </div>
          <div>
            <dt>Organisation</dt>
            <dd>{d.organisation ?? "-"}</dd>
          </div>
          <div>
            <dt>Issuer</dt>
            <dd>{d.issuer ?? "-"}</dd>
          </div>
          <div>
            <dt>Reporting periods</dt>
            <dd>
              {d.periods && d.periods.length
                ? d.periods.map((p, i) => (
                    <div key={i}>
                      {p.label}
                      {p.start || p.end ? <span className="muted"> ({fmtPeriod(p.start, p.end)})</span> : null}
                    </div>
                  ))
                : "-"}
            </dd>
          </div>
          <div>
            <dt>{isPdf ? "Pages" : "Sheets"}</dt>
            <dd>{isPdf ? d.page_count ?? "-" : data.sheets.length ? data.sheets.join(", ") : "-"}</dd>
          </div>
          <div>
            <dt>Facts</dt>
            <dd>
              {data.facts.length ? `${verified} of ${data.facts.length} verified` : "-"}
            </dd>
          </div>
          <div>
            <dt>Processed</dt>
            <dd>
              {fmtTime(d.processed_at)}
              {d.pipeline_version ? <span className="muted"> (pipeline {d.pipeline_version})</span> : null}
            </dd>
          </div>
          <div>
            <dt>Attempts</dt>
            <dd>{d.attempts}</dd>
          </div>
        </dl>
        {d.summary && <p className="summary">{d.summary}</p>}
        {((d.gaps && d.gaps.length > 0) || (d.anomalies && d.anomalies.length > 0)) && (
          <div className="two-col">
            {d.gaps && d.gaps.length > 0 && (
              <div className="callout callout-neutral">
                <div className="callout-title">Gaps noted in the document</div>
                <ul>
                  {d.gaps.map((g, i) => (
                    <li key={i}>{g}</li>
                  ))}
                </ul>
              </div>
            )}
            {d.anomalies && d.anomalies.length > 0 && (
              <div className="callout callout-warn">
                <div className="callout-title">Anomalies</div>
                <ul>
                  {d.anomalies.map((a, i) => {
                    const loc = locationText(a.page, a.sheet, a.cell_range);
                    return (
                      <li key={i}>
                        {a.description}
                        {loc && (
                          <>
                            {" "}
                            <button
                              className="btn-link small"
                              onClick={() =>
                                setSource({
                                  documentId: d.id,
                                  filename: d.filename,
                                  page: a.page,
                                  sheet: a.sheet,
                                  cellRange: a.cell_range,
                                  quote: a.quote,
                                  label: a.description,
                                })
                              }
                            >
                              ({loc})
                            </button>
                          </>
                        )}
                        {a.quote && <div className="quote small">"{a.quote}"</div>}
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>

      <div className="split">
        <div className="split-main stack">
          <section className="card">
            <div className="card-head">
              <h3>Extracted facts</h3>
              <span className="muted small">Click a row to see where it appears in the document.</span>
            </div>
            {d.status !== "succeeded" && <div className="muted pad">Facts appear once processing has succeeded.</div>}
            {d.status === "succeeded" && data.facts.length === 0 && <div className="muted pad">No facts were extracted.</div>}
            {groups.map(([cat, facts]) => (
              <div key={cat} className="fact-group">
                <div className="group-title">
                  {cat.replace(/_/g, " ")} <span className="muted">({facts.length})</span>
                </div>
                <div className="table-wrap">
                  <table className="table table-click">
                    <thead>
                      <tr>
                        <th>Label</th>
                        <th>Value</th>
                        <th>Period</th>
                        <th>Metric / dimension</th>
                        <th>Evidence</th>
                        <th>Source</th>
                      </tr>
                    </thead>
                    <tbody>
                      {facts.map((f) => (
                        <tr
                          key={f.id}
                          className={selected === f.id ? "selected" : undefined}
                          onClick={() => {
                            setSelected(f.id);
                            setSource(factSource(f));
                          }}
                        >
                          <td>
                            {f.label ?? f.key}
                            {f.notes && <div className="muted small">{f.notes}</div>}
                          </td>
                          <td className="value-cell">
                            <span className="strong">{f.value_text ?? "-"}</span>
                            {f.unit && <span className="muted"> {f.unit}</span>}
                            <div className="row gap-xs wrap">
                              <Tag title="Read from the document">{f.origin}</Tag>
                              {f.is_primary === 0 && (
                                <Tag title="History, previous-period or average value; not counted in totals">secondary</Tag>
                              )}
                            </div>
                          </td>
                          <td className="small">{fmtPeriod(f.period_start, f.period_end) || <span className="muted">-</span>}</td>
                          <td className="small">
                            {f.metric ? f.metric.replace(/_/g, " ") : <span className="muted">-</span>}
                            {f.dimension && <div className="muted">{f.dimension}</div>}
                          </td>
                          <td>
                            <StatusPill status={f.evidence_status} />
                          </td>
                          <td className="small nowrap">{locationText(f.page, f.sheet, f.cell_range) || "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </section>

          <section className="card">
            <div className="card-head">
              <h3>Arithmetic checks</h3>
            </div>
            <p className="muted small legend">
              Expected is calculated by the system from the operands; actual (stated) is what the document prints.
              Difference = actual - expected. Rounding means the gap is within the precision of the printed numbers.
            </p>
            {data.checks.length === 0 ? (
              <div className="muted pad">No checks for this document.</div>
            ) : (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Check</th>
                      <th>Status</th>
                      <th>Expected (calculated)</th>
                      <th>Actual (stated)</th>
                      <th className="num">Difference</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.checks.map((c) => (
                      <tr key={c.id}>
                        <td>
                          {c.name}
                          <div className="muted small">{c.source === "table_totals" ? "table total" : "declared relation"}</div>
                        </td>
                        <td>
                          <StatusPill status={c.status} />
                        </td>
                        <td className="mono small">
                          {c.details?.expression ?? (c.expected !== null ? fmtNum(c.expected, 6) : "-")}
                          {c.status === "skipped" && c.details?.reason && <div className="muted">{c.details.reason}</div>}
                        </td>
                        <td className="mono small">{c.details?.stated ?? (c.actual !== null ? fmtNum(c.actual, 6) : "-")}</td>
                        <td className="num mono small">{c.difference !== null ? fmtNum(c.difference, 6) : "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <section className="card">
            <div className="card-head">
              <h3>Processing timeline</h3>
            </div>
            {data.events.length === 0 ? (
              <div className="muted pad">No events.</div>
            ) : (
              <ol className="timeline">
                {data.events.map((e, i) => (
                  <li key={i} className={`tl-${e.level}`}>
                    <span className="tl-time">{fmtTime(e.ts)}</span>
                    <span className="tl-msg">{e.message}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>

        <aside className="split-side">
          <div className="sticky">
            {shown ? (
              <SourceViewer source={shown} onClose={source ? () => { setSource(null); setSelected(null); } : undefined} />
            ) : (
              <div className="card muted pad">The source preview is available once the document has been processed.</div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
