import { fmtNum, type FyGroup, type FyReportData, type FySource } from "../api";
import { locationText, type SourceRef } from "./SourceViewer";
import { StatusPill, Tag } from "./StatusPill";

const MONTHS = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"];

export function fySourceRef(s: FySource, label?: string | null): SourceRef {
  return {
    documentId: s.document_id,
    filename: s.filename,
    page: s.page,
    sheet: s.sheet,
    cellRange: s.cell_range,
    quote: s.quote,
    evidence: s.evidence_status,
    label: label ?? null,
  };
}

function SourceLink({ s, label, onSource }: { s: FySource; label?: string | null; onSource: (r: SourceRef) => void }) {
  return (
    <button className="btn-link small source-link" onClick={() => onSource(fySourceRef(s, label))}>
      {s.filename}
      {locationText(s.page, s.sheet, s.cell_range) ? `, ${locationText(s.page, s.sheet, s.cell_range)}` : ""}
    </button>
  );
}

function MonthStrip({ g }: { g: FyGroup }) {
  return (
    <div className="months" aria-label="Monthly coverage">
      {g.coverage.months.map((m, i) => (
        <div
          key={m.month}
          className={`month month-${m.status}`}
          title={`${m.month}: ${m.covered_days} of ${m.days} days (${m.status})`}
        >
          <span>{MONTHS[i] ?? m.month}</span>
        </div>
      ))}
    </div>
  );
}

function Group({ g, onSource }: { g: FyGroup; onSource: (r: SourceRef) => void }) {
  return (
    <div className="fy-group">
      <div className="fy-group-head">
        <div>
          <div className="strong">{g.organisation}</div>
          <div className="small">
            {g.metric.replace(/_/g, " ")}
            <span className="muted"> · {g.dimension}</span>
            {g.unit && <span className="muted"> · {g.unit}</span>}
          </div>
        </div>
        <div className="fy-total">
          <div className="big">
            {fmtNum(g.total, 2)} <span className="muted small">{g.unit ?? ""}</span>
          </div>
          <div className="row gap-xs end">
            <Tag title="Summed by the system from the records below">calculated</Tag>
            {g.includes_prorated && <Tag title="At least one record was prorated by days">includes prorated</Tag>}
            {g.unverified_records > 0 && <StatusPill status="unverified" label={`${g.unverified_records} unverified`} />}
          </div>
        </div>
      </div>
      <div className="mono small calc">{g.calculation}</div>
      <div className="coverage">
        <div className="small">
          Coverage: <span className="strong">{g.coverage.months_covered} of 12 months</span>
          <span className="muted">
            {" "}
            ({g.coverage.covered_days} of {g.coverage.fy_days} days)
          </span>
        </div>
        <MonthStrip g={g} />
        <div className="legend-row small muted">
          <span className="month-key month-covered" /> covered
          <span className="month-key month-partial" /> partial
          <span className="month-key month-missing" /> missing
        </div>
      </div>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Record</th>
              <th>Period</th>
              <th className="num">Value</th>
              <th className="num">Share in FY</th>
              <th className="num">Contribution</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {g.records.map((r, i) => (
              <tr key={i}>
                <td>
                  {r.label ?? "-"}
                  {r.prorated && (
                    <>
                      {" "}
                      <Tag title={`${r.included_days} days fall inside the financial year`}>prorated</Tag>
                    </>
                  )}
                </td>
                <td className="small nowrap">
                  {r.period_start} to {r.period_end}
                </td>
                <td className="num">{fmtNum(r.value, 4)}</td>
                <td className="num">{(r.share * 100).toFixed(r.share === 1 ? 0 : 1)}%</td>
                <td className="num">{fmtNum(r.contribution, 4)}</td>
                <td>
                  <div className="row gap-xs wrap">
                    {r.source.evidence_status && <StatusPill status={r.source.evidence_status} />}
                    <SourceLink s={r.source} label={r.label} onSource={onSource} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {g.cross_checks.length > 0 && (
        <div className="cross">
          <div className="small strong">Cross-checks against stated aggregates</div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Aggregate</th>
                  <th className="num">Stated</th>
                  <th className="num">Sum of parts</th>
                  <th className="num">Difference</th>
                  <th>Result</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {g.cross_checks.map((c, i) => (
                  <tr key={i}>
                    <td className="small">{c.description}</td>
                    <td className="num">{fmtNum(c.stated, 4)}</td>
                    <td className="num">{fmtNum(c.sum_of_parts, 4)}</td>
                    <td className="num">{fmtNum(c.difference, 4)}</td>
                    <td>
                      <StatusPill status={c.status} />
                    </td>
                    <td>
                      <SourceLink s={c.stated_ref} label={c.description} onSource={onSource} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export function FyReport({ report, onSource }: { report: FyReportData; onSource: (r: SourceRef) => void }) {
  const outside = Object.entries(report.documents_outside_fy ?? {});
  return (
    <div className="stack">
      <div className="row between wrap gap-s">
        <div>
          <h3 className="inline">{report.financial_year}</h3>{" "}
          <span className="muted small">
            {report.start} to {report.end}
          </span>
        </div>
        {report.organisations.length > 0 && (
          <span className="small muted">Organisations: {report.organisations.join("; ")}</span>
        )}
      </div>
      {report.notes.length > 0 && (
        <div className="callout callout-warn">
          <div className="callout-title">Notes</div>
          <ul>
            {report.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      {report.not_ready && report.not_ready.length > 0 && (
        <div className="callout callout-neutral">
          <div className="callout-title">Not processed yet (not included)</div>
          <ul>
            {report.not_ready.map((d) => (
              <li key={d.id}>
                {d.filename} <StatusPill status={d.status} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {report.groups.length === 0 && (
        <div className="muted pad">No totalable records fall inside this financial year for the selected documents.</div>
      )}
      {report.groups.map((g, i) => (
        <Group key={`${g.organisation}-${g.metric}-${g.dimension}-${g.unit}-${i}`} g={g} onSource={onSource} />
      ))}
      {report.excluded.length > 0 && (
        <div>
          <div className="group-title">Excluded records ({report.excluded.length})</div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Record</th>
                  <th>Period</th>
                  <th className="num">Value</th>
                  <th>Reason</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {report.excluded.map((e, i) => (
                  <tr key={i}>
                    <td>
                      {e.label ?? "-"}
                      {e.dimension && <div className="muted small">{e.dimension}</div>}
                    </td>
                    <td className="small nowrap">
                      {e.period_start} to {e.period_end}
                    </td>
                    <td className="num">
                      {fmtNum(e.value, 4)} <span className="muted small">{e.unit ?? ""}</span>
                    </td>
                    <td className="small">{e.reason}</td>
                    <td>
                      <SourceLink s={e.source} label={e.label} onSource={onSource} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {outside.length > 0 && (
        <div className="callout callout-neutral">
          <div className="callout-title">Documents with data only in other financial years</div>
          <ul>
            {outside.map(([f, years]) => (
              <li key={f}>
                {f}: <span className="muted">{years.join(", ")}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
