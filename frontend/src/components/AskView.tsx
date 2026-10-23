import { useEffect, useRef, useState } from "react";
import {
  api,
  errorMessage,
  fmtNum,
  type AnswerCitation,
  type AskResponse,
  type Health,
} from "../api";
import { DocPicker, useDocSelection } from "./DocPicker";
import { FyReport } from "./FyReport";
import { Markdown } from "./Markdown";
import { locationText, SourceDrawer, type SourceRef } from "./SourceViewer";
import { StatusPill, Tag } from "./StatusPill";

const EXAMPLES = [
  "What electricity consumption does each bill report, and for which period?",
  "Does the water consumption match the meter readings?",
  "What total waste quantity is recorded?",
  "Do the workbook's monthly values and annual summaries agree?",
  "Does the quality policy specify a deadline for resolving customer complaints?",
  "What is the total electricity consumption for FY 2024-25?",
];

function citationSource(c: AnswerCitation): SourceRef | null {
  if (!c.document_id) return null;
  return {
    documentId: c.document_id,
    filename: c.filename,
    page: c.page,
    sheet: c.sheet,
    cellRange: c.cell_range,
    bbox: c.bbox ?? null,
    quote: c.quote,
    evidence: c.verification,
    note: c.note,
  };
}

/** The schema says "indexes into citations" without fixing the base; detect 1-based use. */
function indexBase(res: AskResponse): 0 | 1 {
  const all = res.key_values.flatMap((k) => k.citation_indexes);
  if (!all.length) return 0;
  const n = res.citations.length;
  if (!all.includes(0) && all.some((i) => i === n) && all.every((i) => i >= 1 && i <= n)) return 1;
  return 0;
}

function Callout({ title, items, tone }: { title: string; items: string[]; tone: "warn" | "neutral" | "bad" }) {
  if (!items.length) return null;
  return (
    <div className={`callout callout-${tone}`}>
      <div className="callout-title">{title}</div>
      <ul>
        {items.map((t, i) => (
          <li key={i}>
            <Markdown text={t} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function Answer({ res, onSource }: { res: AskResponse; onSource: (s: SourceRef) => void }) {
  const base = indexBase(res);
  const s = res.support ?? {};
  const open = (i: number) => {
    const c = res.citations[i];
    const src = c && citationSource(c);
    if (src) onSource(src);
  };
  return (
    <div className="stack">
      <div className="row between wrap gap-s">
        <div className="row gap-s wrap">
          <StatusPill status={res.status} />
          {res.financial_year && <Tag>{res.financial_year}</Tag>}
          <span className="muted small">
            {res.documents.length} document{res.documents.length === 1 ? "" : "s"} · {res.elapsed_s}s
          </span>
        </div>
        {s.citations !== undefined && (
          <div className="small support">
            {s.verified ?? 0} of {s.citations ?? 0} citations verified
            {s.partial ? `, ${s.partial} partial` : ""}
            {s.unverified ? `, ${s.unverified} unverified` : ""}; {s.calculations_ok ?? 0} of {s.calculations ?? 0}{" "}
            calculations re-checked
          </div>
        )}
      </div>

      <div className="answer">
        <Markdown text={res.answer} />
      </div>

      {res.not_ready.length > 0 && (
        <Callout
          title="Selected but not processed yet (no evidence used)"
          items={res.not_ready.map((d) => `${d.filename} (${d.status})`)}
          tone="neutral"
        />
      )}

      {res.key_values.length > 0 && (
        <div>
          <div className="group-title">Key values</div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Label</th>
                  <th>Value</th>
                  <th>Origin</th>
                  <th>Citations</th>
                </tr>
              </thead>
              <tbody>
                {res.key_values.map((k, i) => (
                  <tr key={i}>
                    <td>{k.label}</td>
                    <td>
                      <span className="strong">{k.value}</span>
                      {k.unit && <span className="muted"> {k.unit}</span>}
                    </td>
                    <td>
                      <Tag title={k.origin === "calculated" ? "Derived by arithmetic" : "Printed in the document"}>{k.origin}</Tag>
                    </td>
                    <td>
                      <div className="row gap-xs wrap">
                        {k.citation_indexes.length === 0 && <span className="muted small">none</span>}
                        {k.citation_indexes.map((ci) => {
                          const idx = ci - base;
                          const c = res.citations[idx];
                          return c ? (
                            <button key={ci} className="btn-link small" onClick={() => open(idx)} title={c.quote}>
                              [{idx + 1}]
                            </button>
                          ) : (
                            <span key={ci} className="muted small" title="No such citation">
                              [?]
                            </span>
                          );
                        })}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {res.calculations.length > 0 && (
        <div>
          <div className="group-title">Calculations</div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Description</th>
                  <th>Expression = stated result</th>
                  <th className="num">Recomputed</th>
                  <th>Check</th>
                </tr>
              </thead>
              <tbody>
                {res.calculations.map((c, i) => (
                  <tr key={i}>
                    <td>{c.description}</td>
                    <td className="mono small">
                      {c.expression} = {fmtNum(c.result, 6)}
                      {c.unit ? ` ${c.unit}` : ""}
                    </td>
                    <td className="num mono small">{c.recomputed === null || c.recomputed === undefined ? "could not evaluate" : fmtNum(c.recomputed, 6)}</td>
                    <td>
                      <StatusPill status={c.ok ? "ok" : "mismatch"} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="two-col">
        <Callout title="Gaps" items={res.gaps} tone="neutral" />
        <Callout title="Inconsistencies" items={res.inconsistencies} tone="bad" />
        <Callout title="Assumptions" items={res.assumptions} tone="warn" />
      </div>

      {res.citations.length > 0 && (
        <div>
          <div className="group-title">Citations</div>
          <ol className="citations">
            {res.citations.map((c, i) => (
              <li key={i}>
                <button className="citation" onClick={() => open(i)} disabled={!c.document_id}>
                  <span className="cite-num">[{i + 1}]</span>
                  <span className="cite-body">
                    <span className="row gap-s wrap">
                      <span className="strong">{c.filename ?? "Unknown document"}</span>
                      <span className="muted small">{locationText(c.page, c.sheet, c.cell_range)}</span>
                      <StatusPill status={c.verification} />
                    </span>
                    <span className="quote small">"{c.quote}"</span>
                    {c.note && <span className="muted small">{c.note}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </div>
      )}

      {res.fy_report && (
        <div className="card inset">
          <div className="group-title">Financial-year report used as evidence</div>
          <FyReport report={res.fy_report} onSource={onSource} />
        </div>
      )}
    </div>
  );
}

export function AskView({ active }: { active: boolean }) {
  const { docs, error: docsErr, selected, setSelected } = useDocSelection(active);
  const [years, setYears] = useState<string[]>([]);
  const [fy, setFy] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [res, setRes] = useState<AskResponse | null>(null);
  const [source, setSource] = useState<SourceRef | null>(null);
  const started = useRef(0);

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined);
  }, []);

  const idsKey = [...selected].sort().join(",");
  useEffect(() => {
    if (!selected.size) {
      setYears([]);
      return;
    }
    let live = true;
    api
      .fyYears([...selected])
      .then((r) => live && setYears(r.years))
      .catch(() => live && setYears([]));
    return () => {
      live = false;
    };
  }, [idsKey]);

  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(() => setElapsed(Math.floor((Date.now() - started.current) / 1000)), 250);
    return () => window.clearInterval(t);
  }, [busy]);

  const submit = async () => {
    const q = question.trim();
    if (!q || busy) return;
    if (selected.size === 0) {
      setErr("Select at least one processed document.");
      return;
    }
    setBusy(true);
    setErr(null);
    setRes(null);
    setSource(null);
    started.current = Date.now();
    setElapsed(0);
    try {
      const r = await api.ask({
        question: q,
        document_ids: [...selected],
        ...(fy.trim() ? { financial_year: fy.trim() } : {}),
      });
      setRes(r);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <h2>Ask a question</h2>
          {health && <span className="muted small">Model {health.model}</span>}
        </div>
        <div className="ask-grid">
          <div>
            <div className="field-label">Documents</div>
            {docsErr && <div className="error-box">{docsErr}</div>}
            <DocPicker docs={docs} selected={selected} onChange={setSelected} />
          </div>
          <form
            className="stack-s"
            onSubmit={(e) => {
              e.preventDefault();
              submit();
            }}
          >
            <label className="field-label" htmlFor="question">
              Question
            </label>
            <textarea
              id="question"
              rows={3}
              value={question}
              placeholder="Ask about consumption, totals, periods, checks..."
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
              }}
            />
            <div className="chips">
              {EXAMPLES.map((q) => (
                <button type="button" key={q} className="chip" onClick={() => setQuestion(q)}>
                  {q}
                </button>
              ))}
            </div>
            <div className="row gap-s wrap">
              <label className="small">
                Financial year{" "}
                <input
                  list="fy-options"
                  value={fy}
                  onChange={(e) => setFy(e.target.value)}
                  placeholder="optional, e.g. FY2024-25"
                  size={20}
                />
                <datalist id="fy-options">
                  {years.map((y) => (
                    <option key={y} value={y} />
                  ))}
                </datalist>
              </label>
              <button className="btn btn-primary" type="submit" disabled={busy || !question.trim()}>
                {busy ? "Working..." : "Ask"}
              </button>
            </div>
            <div className="muted small">
              If no year is given, one named in the question (for example "FY 2024-25") is used.
            </div>
          </form>
        </div>
      </section>

      {busy && (
        <section className="card row gap-s">
          <span className="spinner" aria-hidden="true" />
          <span>
            Gathering evidence and asking the model... {elapsed}s
            <span className="muted small"> (usually 10 to 40 seconds)</span>
          </span>
        </section>
      )}
      {err && <div className="error-box">Request failed: {err}</div>}
      {res && (
        <section className="card">
          <div className="muted small question-echo">Q: {res.question}</div>
          <Answer res={res} onSource={setSource} />
        </section>
      )}
      <SourceDrawer source={source} onClose={() => setSource(null)} />
    </div>
  );
}
