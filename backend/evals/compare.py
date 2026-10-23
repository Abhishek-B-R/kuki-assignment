"""Write evals/results/comparison.md from baseline.json and improved.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

RESULTS = Path(__file__).resolve().parent / "results"
DIMS = ["answer", "status", "evidence", "uncertainty", "calculation"]


def cell(v) -> str:
    return "-" if v is None else f"{v:.2f}"


def runs(name: str) -> list[dict]:
    """The main run plus any answer-only repeats (name-r2, name-r3, ...) on the same extracted documents."""
    paths = [RESULTS / f"{name}.json", *sorted(RESULTS.glob(f"{name}-r*.json"))]
    return [json.loads(p.read_text()) for p in paths if p.exists()]


def main() -> int:
    base_runs, imp_runs = runs("baseline"), runs("improved")
    base, imp = base_runs[0], imp_runs[0]
    lines = [
        "# Baseline vs improved",
        "",
        f"- Baseline: `{base['version']}` with {base['model']}, run {base['ran_at']}",
        f"- Improved: `{imp['version']}` with {imp['model']}, run {imp['ran_at']}"
        + (f" (documents from `{imp['reused_extraction_from']}`, re-extracted: {', '.join(imp.get('reextracted') or []) or 'none'})"
           if imp.get("reused_extraction_from") else ""),
        "",
        "## Overall",
        "",
        "| Metric | Baseline | Improved | Change |",
        "|---|---|---|---|",
    ]
    bs, is_ = base["summary"], imp["summary"]
    lines.append(f"| Cases passed | {bs['passed']}/{bs['cases']} | {is_['passed']}/{is_['cases']} | {is_['passed'] - bs['passed']:+d} |")
    for d in ["overall", *DIMS]:
        b, i = bs.get(d), is_.get(d)
        delta = "-" if b is None or i is None else f"{i - b:+.2f}"
        lines.append(f"| {d} | {cell(b)} | {cell(i)} | {delta} |")
    if len(base_runs) > 1 or len(imp_runs) > 1:
        lines += ["", "## Repeat runs", "",
                  "The answer step is rerun on the same extracted documents to show how much of a difference is model noise.", "",
                  "| Pipeline | Runs | Passed per run | Mean passed | Overall per run | Mean overall |", "|---|---|---|---|---|---|"]
        for label, rs in (("Baseline", base_runs), ("Improved", imp_runs)):
            passed = [r["summary"]["passed"] for r in rs]
            overall = [r["summary"]["overall"] for r in rs]
            lines.append(f"| {label} | {len(rs)} | {', '.join(map(str, passed))} | {sum(passed) / len(rs):.1f}/{rs[0]['summary']['cases']}"
                         f" | {', '.join(f'{o:.2f}' for o in overall)} | {sum(overall) / len(rs):.2f} |")

    def hits(rs: list[dict], cid: str) -> list[bool]:
        return [c["score"]["passed"] for r in rs for c in r["cases"] if c["id"] == cid]

    def passes(rs: list[dict], cid: str) -> str:
        h = hits(rs, cid)
        return f"{sum(h)}/{len(h)}"

    # A case can only be affected by the change if its evidence includes a re-extracted document. The others
    # get byte-identical model input in both pipelines, so any difference there is run-to-run noise.
    cases = {c["id"]: c for c in yaml.safe_load((RESULTS.parent / "cases.yaml").read_text())}
    changed = set(imp.get("reextracted") or [])

    def touched(cid: str) -> bool:
        docs = cases[cid]["documents"]
        return bool(changed) and (docs == "all" or bool(changed & set(docs)))

    ids = [c["id"] for c in base["cases"]]
    lines += ["", "## Where the difference comes from", "",
              "| Cases | Count | Baseline pass rate | Improved pass rate |", "|---|---|---|---|"]
    for label, group in (("Evidence includes a re-extracted document", [i for i in ids if touched(i)]),
                         ("Identical evidence in both pipelines (noise control)", [i for i in ids if not touched(i)])):
        b = [x for i in group for x in hits(base_runs, i)]
        m = [x for i in group for x in hits(imp_runs, i)]
        if group:
            lines.append(f"| {label} | {len(group)} | {sum(b)}/{len(b)} ({sum(b) / len(b):.0%}) | {sum(m)}/{len(m)} ({sum(m) / len(m):.0%}) |")

    lines += ["", "## Per case", "", "| Case | Touched by change | Baseline | Improved | Change | Status (base -> improved) | Passes across runs (base / improved) |",
              "|---|---|---|---|---|---|---|"]
    imp_by = {c["id"]: c for c in imp["cases"]}
    for c in base["cases"]:
        o = imp_by.get(c["id"])
        if not o:
            continue
        b, i = c["score"], o["score"]
        tag = lambda s: ("PASS" if s["passed"] else "FAIL") + f" {s['overall']:.2f}"
        lines.append(
            f"| {c['id']} {c['title']} | {'yes' if touched(c['id']) else 'no'} | {tag(b)} | {tag(i)} | {i['overall'] - b['overall']:+.2f} |"
            f" {b['detail']['status']} -> {i['detail']['status']} | {passes(base_runs, c['id'])} / {passes(imp_runs, c['id'])} |"
        )
    (RESULTS / "comparison.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
