import { useEffect, useMemo, useState } from "react";
import { api, errorMessage, type FyReportData } from "../api";
import { DocPicker, useDocSelection } from "./DocPicker";
import { FyReport } from "./FyReport";
import { SourceDrawer, type SourceRef } from "./SourceViewer";

export function FyView({ active }: { active: boolean }) {
  const { docs, error: docsErr, selected, setSelected } = useDocSelection(active);
  const [filter, setFilter] = useState(false);
  const [years, setYears] = useState<string[] | null>(null);
  const [label, setLabel] = useState("");
  const [report, setReport] = useState<FyReportData | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [source, setSource] = useState<SourceRef | null>(null);

  const ids = useMemo(() => (filter ? [...selected] : null), [filter, selected]);
  const idsKey = ids ? ids.slice().sort().join(",") : "*";

  useEffect(() => {
    if (!active) return;
    let live = true;
    if (ids && ids.length === 0) {
      setYears([]);
      return;
    }
    api
      .fyYears(ids)
      .then((r) => {
        if (!live) return;
        setYears(r.years);
        setLabel((cur) => cur || r.years[r.years.length - 1] || "");
      })
      .catch((e) => live && setErr(errorMessage(e)));
    return () => {
      live = false;
    };
  }, [idsKey, active]);

  const run = async (fy: string) => {
    const l = fy.trim();
    if (!l) return;
    if (ids && ids.length === 0) {
      setErr("Select at least one document, or turn the document filter off.");
      return;
    }
    setLoading(true);
    setErr(null);
    try {
      setReport(await api.fy(l, ids));
    } catch (e) {
      setReport(null);
      setErr(errorMessage(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <h2>Financial year totals</h2>
        </div>
        <p className="muted small legend">
          Indian financial year (1 April to 31 March). Totals are summed by the system from extracted primary records,
          kept separate per organisation, metric, dimension and unit; periods crossing the year boundary are prorated by
          days.
        </p>
        <form
          className="row gap-s wrap"
          onSubmit={(e) => {
            e.preventDefault();
            run(label);
          }}
        >
          <label className="small">
            Year{" "}
            <select
              value={years && years.includes(label) ? label : ""}
              onChange={(e) => {
                setLabel(e.target.value);
                if (e.target.value) run(e.target.value);
              }}
            >
              <option value="">{years === null ? "Loading..." : years.length ? "Choose a year" : "No years with data"}</option>
              {(years ?? []).map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </label>
          <label className="small">
            or type{" "}
            <input
              type="text"
              value={label}
              placeholder="FY2024-25"
              onChange={(e) => setLabel(e.target.value)}
              size={12}
            />
          </label>
          <button className="btn btn-primary" type="submit" disabled={loading || !label.trim()}>
            {loading ? "Loading..." : "Show"}
          </button>
          <label className="small row gap-xs">
            <input type="checkbox" checked={filter} onChange={(e) => setFilter(e.target.checked)} />
            Limit to selected documents
          </label>
        </form>
        {filter && (
          <div className="mt">
            {docsErr && <div className="error-box">{docsErr}</div>}
            <DocPicker docs={docs} selected={selected} onChange={setSelected} />
          </div>
        )}
        {err && <div className="error-box">{err}</div>}
      </section>
      {report && (
        <section className="card">
          <FyReport report={report} onSource={setSource} />
        </section>
      )}
      <SourceDrawer source={source} onClose={() => setSource(null)} />
    </div>
  );
}
