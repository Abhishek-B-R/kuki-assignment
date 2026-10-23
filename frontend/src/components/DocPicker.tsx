import { useEffect, useRef, useState } from "react";
import { api, errorMessage, type DocumentRow } from "../api";
import { StatusPill } from "./StatusPill";

/** Checkbox list of documents. Unprocessed documents are listed but cannot be selected. */
export function DocPicker({
  docs,
  selected,
  onChange,
}: {
  docs: DocumentRow[];
  selected: Set<string>;
  onChange: (next: Set<string>) => void;
}) {
  const ready = docs.filter((d) => d.status === "succeeded");
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    onChange(next);
  };
  return (
    <div className="picker">
      <div className="row gap-s small">
        <span className="muted">
          {selected.size} of {ready.length} processed documents selected
        </span>
        <button type="button" className="btn-link small" onClick={() => onChange(new Set(ready.map((d) => d.id)))}>
          All
        </button>
        <button type="button" className="btn-link small" onClick={() => onChange(new Set())}>
          None
        </button>
      </div>
      {docs.length === 0 && <div className="muted small">No documents uploaded.</div>}
      <ul className="picker-list">
        {docs.map((d) => {
          const ok = d.status === "succeeded";
          return (
            <li key={d.id}>
              <label className={ok ? undefined : "muted"}>
                <input type="checkbox" disabled={!ok} checked={selected.has(d.id)} onChange={() => toggle(d.id)} />
                <span className="picker-name" title={d.filename}>
                  {d.filename}
                </span>
                {d.organisation && <span className="muted small"> · {d.organisation}</span>}
                {!ok && (
                  <>
                    {" "}
                    <StatusPill status={d.status} />
                  </>
                )}
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Loads the document list whenever `active` turns true. The first load selects every processed
 * document; later loads keep the user's choice and drop documents that no longer exist. */
export function useDocSelection(active: boolean) {
  const [docs, setDocs] = useState<DocumentRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const loaded = useRef(false);

  useEffect(() => {
    if (!active) return;
    let live = true;
    api
      .documents()
      .then((d) => {
        if (!live) return;
        setDocs(d.documents);
        setError(null);
        const ready = d.documents.filter((x) => x.status === "succeeded").map((x) => x.id);
        if (!loaded.current) {
          loaded.current = ready.length > 0;
          setSelected(new Set(ready));
        } else {
          const ok = new Set(ready);
          setSelected((cur) => new Set([...cur].filter((id) => ok.has(id))));
        }
      })
      .catch((e) => live && setError(errorMessage(e)));
    return () => {
      live = false;
    };
  }, [active]);

  return { docs, error, selected, setSelected };
}
