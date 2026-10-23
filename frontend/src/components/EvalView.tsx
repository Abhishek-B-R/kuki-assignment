import { Fragment, useEffect, useState } from "react";
import { api, errorMessage, type EvalCaseDef, type EvalCaseResult, type EvalRun, type EvalsResponse } from "../api";
import { Markdown } from "./Markdown";
import { StatusPill } from "./StatusPill";

const DIMS = ["overall", "answer", "status", "evidence", "uncertainty", "calculation"] as const;

function num(v: number | null | undefined): string {
  return v === null || v === undefined ? "-" : v.toFixed(2);
}

function Delta({ a, b }: { a: number | null | undefined; b: number | null | undefined }) {
  if (a === null || a === undefined || b === null || b === undefined) return <span className="muted">-</span>;
  const d = b - a;
  const cls = Math.abs(d) < 0.005 ? "muted" : d > 0 ? "delta-up" : "delta-down";
  return <span className={cls}>{(d >= 0 ? "+" : "") + d.toFixed(2)}</span>;
}

function PassFail({ r }: { r: EvalCaseResult | undefined }) {
  if (!r) return <span className="muted">-</span>;
  return (
    <span className="row gap-xs">
      <span className="mono">{r.score.overall.toFixed(2)}</span>
      <StatusPill status={r.score.passed ? "pass" : "fail"} label={r.score.passed ? "PASS" : "FAIL"} />
    </span>
  );
}

function asText(v: unknown): string {
  if (Array.isArray(v)) return v.map(asText).join(" / ");
  if (v && typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function RunMeta({ run }: { run: EvalRun }) {
  return (
    <div className="run-meta">
      <div className="strong">{run.name}</div>
      <div className="small">
        version <span className="mono">{run.version}</span> · {run.model}
      </div>
      <div className="small muted">
        ran {new Date(run.ran_at).toLocaleString()}
        {run.reused_extraction_from ? ` · extraction reused from ${run.reused_extraction_from}` : ""}
      </div>
    </div>
  );
}

function CaseSide({ title, r }: { title: string; r: EvalCaseResult | undefined }) {
  if (!r) {
    return (
      <div className="case-side">
        <div className="group-title">{title}</div>
        <div className="muted small">Not run.</div>
      </div>
    );
  }
  const a = r.answer ?? {};
  const cites = a.citations ?? [];
  const verified = cites.filter((c) => c.verification === "verified").length;
  const partial = cites.filter((c) => c.verification === "partial").length;
  const det = r.score.detail ?? {};
  return (
    <div className="case-side">
      <div className="row between wrap gap-s">
        <div className="group-title">{title}</div>
        <div className="row gap-xs">
          <StatusPill status={a.status ?? "error"} />
          <PassFail r={r} />
        </div>
      </div>
      <div className="answer small">
        <Markdown text={a.answer ?? ""} />
      </div>
      <div className="small">
        Citations: {cites.length} ({verified} verified{partial ? `, ${partial} partial` : ""})
        {det.evidence && (
          <span className="muted">
            {" "}
            · expected sources hit {det.evidence.sources_hit} of {det.evidence.sources}
          </span>
        )}
      </div>
      <table className="table compact">
        <tbody>
          {Object.entries(r.score.scores).map(([k, v]) => (
            <tr key={k}>
              <td>{k}</td>
              <td className="num mono">{num(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {(det.missing?.length ?? 0) > 0 && (
        <div className="small">
          <span className="strong">Missing items:</span>{" "}
          {det.missing!.map((m, i) => (
            <code key={i} className="code-chip">
              {asText(m)}
            </code>
          ))}
        </div>
      )}
      {(det.flags_missing?.length ?? 0) > 0 && (
        <div className="small">
          <span className="strong">Flags not raised:</span>{" "}
          {det.flags_missing!.map((m, i) => (
            <code key={i} className="code-chip">
              {asText(m)}
            </code>
          ))}
        </div>
      )}
      {(det.sources_missed?.length ?? 0) > 0 && (
        <div className="small">
          <span className="strong">Sources missed:</span>
          <ul>
            {det.sources_missed!.map((s, i) => (
              <li key={i}>
                {String(s.doc ?? "")}
                {s.pages ? ` pages ${asText(s.pages)}` : ""}
                {s.sheet ? ` ${String(s.sheet)}!${String(s.cells ?? "")}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
      {(det.violations?.length ?? 0) > 0 && (
        <div className="small error-inline">Wrong claims matched: {det.violations!.join("; ")}</div>
      )}
      {(det.calculations?.length ?? 0) > 0 && (
        <div className="small">
          <span className="strong">Calculations:</span>
          <ul>
            {det.calculations!.map((c, i) => (
              <li key={i} className="row gap-xs wrap">
                <span className="mono">
                  {c.expression} = {c.result}
                </span>
                <StatusPill status={c.ok ? "ok" : "mismatch"} />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ExtractionTable({ run }: { run: EvalRun }) {
  return (
    <div>
      <div className="group-title">Extraction snapshot: {run.name}</div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>File</th>
              <th>Status</th>
              <th>Type</th>
              <th>Organisation</th>
              <th className="num">Attempts</th>
              <th className="num">Facts verified</th>
              <th className="num">Failed checks</th>
            </tr>
          </thead>
          <tbody>
            {run.extraction.map((e) => (
              <tr key={e.filename}>
                <td>
                  {e.filename}
                  {e.error && <div className="error-inline small">{e.error}</div>}
                </td>
                <td>
                  <StatusPill status={e.status} />
                </td>
                <td>{e.doc_type?.replace(/_/g, " ") ?? "-"}</td>
                <td>{e.organisation ?? "-"}</td>
                <td className="num">{e.attempts}</td>
                <td className="num">
                  {e.verified_facts} / {e.facts}
                </td>
                <td className="num">{e.failed_checks}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function EvalView() {
  const [data, setData] = useState<EvalsResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api
      .evals()
      .then(setData)
      .catch((e) => setErr(errorMessage(e)));
  }, []);

  if (err) return <div className="error-box">Could not load evaluation results: {err}</div>;
  if (!data) return <div className="muted pad">Loading...</div>;

  const base = data.runs.baseline;
  const imp = data.runs.improved;
  const baseRepeats = data.repeats?.baseline ?? [];
  const impRepeats = data.repeats?.improved ?? [];
  if (!base && !imp) {
    return (
      <section className="card">
        <h2>Evaluation</h2>
        <p>
          No results yet: run <code>make eval-baseline</code> and <code>make eval-improved</code>.
        </p>
      </section>
    );
  }

  const caseDefs = new Map<string, EvalCaseDef>((data.cases ?? []).map((c) => [c.id, c]));
  const bBy = new Map((base?.cases ?? []).map((c) => [c.id, c]));
  const iBy = new Map((imp?.cases ?? []).map((c) => [c.id, c]));
  const ids = [...new Set([...(base?.cases ?? []).map((c) => c.id), ...(imp?.cases ?? []).map((c) => c.id)])];

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <h2>Evaluation: baseline vs improved</h2>
        </div>
        {(!base || !imp) && (
          <div className="warn-box">
            Only the {base ? "baseline" : "improved"} run has results; run <code>make eval-baseline</code> and <code>make eval-improved</code> to produce both.
          </div>
        )}
        <div className="row gap-m wrap">
          {base && <RunMeta run={base} />}
          {imp && <RunMeta run={imp} />}
        </div>
        <div className="table-wrap mt">
          <table className="table">
            <thead>
              <tr>
                <th>Metric</th>
                <th className="num">Baseline</th>
                <th className="num">Improved</th>
                <th className="num">Change</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Cases passed</td>
                <td className="num">{base ? `${base.summary.passed}/${base.summary.cases}` : "-"}</td>
                <td className="num">{imp ? `${imp.summary.passed}/${imp.summary.cases}` : "-"}</td>
                <td className="num">
                  {base && imp ? (
                    <span className={imp.summary.passed - base.summary.passed > 0 ? "delta-up" : imp.summary.passed < base.summary.passed ? "delta-down" : "muted"}>
                      {(imp.summary.passed - base.summary.passed >= 0 ? "+" : "") + (imp.summary.passed - base.summary.passed)}
                    </span>
                  ) : (
                    "-"
                  )}
                </td>
              </tr>
              {(baseRepeats.length > 0 || impRepeats.length > 0) && (
                <tr>
                  <td>
                    Passed, all runs <span className="muted small">(answer step repeated on the same extraction)</span>
                  </td>
                  <td className="num mono">{base ? [base.summary, ...baseRepeats.map((r) => r.summary)].map((x) => x.passed).join(", ") : "-"}</td>
                  <td className="num mono">{imp ? [imp.summary, ...impRepeats.map((r) => r.summary)].map((x) => x.passed).join(", ") : "-"}</td>
                  <td className="num muted">-</td>
                </tr>
              )}
              {DIMS.map((d) => (
                <tr key={d}>
                  <td>{d}</td>
                  <td className="num mono">{num(base?.summary[d])}</td>
                  <td className="num mono">{num(imp?.summary[d])}</td>
                  <td className="num mono">
                    <Delta a={base?.summary[d]} b={imp?.summary[d]} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="muted small legend">
          Scores are computed deterministically by evals/score.py: answer 35%, status 15%, evidence 20%, uncertainty 20%,
          calculation 10% (dimensions that do not apply to a case are left out). A case passes at 0.80 overall with the
          right status and no wrong claims.
        </p>
      </section>

      <section className="card">
        <div className="card-head">
          <h3>Per case</h3>
          <span className="muted small">Click a row for the question, expected answer and both answers.</span>
        </div>
        <div className="table-wrap">
          <table className="table table-click">
            <thead>
              <tr>
                <th>Case</th>
                <th>Baseline</th>
                <th>Improved</th>
                <th className="num">Change</th>
                <th>Status (base to improved)</th>
              </tr>
            </thead>
            <tbody>
              {ids.map((id) => {
                const b = bBy.get(id);
                const i = iBy.get(id);
                const title = b?.title ?? i?.title ?? caseDefs.get(id)?.title ?? "";
                const def = caseDefs.get(id);
                const isOpen = open === id;
                return (
                  <Fragment key={id}>
                    <tr className={isOpen ? "selected" : undefined} onClick={() => setOpen(isOpen ? null : id)}>
                      <td>
                        <span className="mono strong">{id}</span> {title}
                      </td>
                      <td>
                        <PassFail r={b} />
                      </td>
                      <td>
                        <PassFail r={i} />
                      </td>
                      <td className="num mono">
                        <Delta a={b?.score.overall} b={i?.score.overall} />
                      </td>
                      <td className="small">
                        {b?.score.detail.status ?? b?.answer?.status ?? "-"} to {i?.score.detail.status ?? i?.answer?.status ?? "-"}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="expand">
                        <td colSpan={5}>
                          <div className="stack-s">
                            <div>
                              <span className="field-label">Question</span> {b?.question ?? i?.question ?? def?.question}
                            </div>
                            {def && (
                              <div className="small muted">
                                Documents: {Array.isArray(def.documents) ? def.documents.join(", ") : def.documents}
                                {def.expected_status ? ` · expected status: ${def.expected_status.join(" or ")}` : ""}
                              </div>
                            )}
                            {def?.expected_answer && (
                              <div className="callout callout-neutral">
                                <div className="callout-title">Expected answer (checked by hand)</div>
                                <div className="small">{def.expected_answer}</div>
                              </div>
                            )}
                            <div className="two-col">
                              <CaseSide title="Baseline" r={b} />
                              <CaseSide title="Improved" r={i} />
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="card stack">
        {base && <ExtractionTable run={base} />}
        {imp && <ExtractionTable run={imp} />}
      </section>
    </div>
  );
}
