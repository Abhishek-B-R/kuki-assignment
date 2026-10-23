"""Re-score saved runs with the current cases.yaml and scorer, without calling the model.

    uv run python -m evals.rescore            # every results/*.json (and *.partial.json)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from .score import score_case, summarise

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"


def rescore(path: Path, cases: dict[str, dict]) -> None:
    data = json.loads(path.read_text())
    rows = data if isinstance(data, list) else data["cases"]
    for r in rows:
        r["score"] = score_case(cases[r["id"]], r["answer"])
    if isinstance(data, dict):
        data["summary"] = summarise(rows)
    path.write_text(json.dumps(data, indent=1 if isinstance(data, list) else 2, ensure_ascii=False, default=str))
    s = summarise(rows)
    print(f"{path.name}: {s['passed']}/{s['cases']} passed, overall {s['overall']:.3f}")


def main() -> int:
    cases = {c["id"]: c for c in yaml.safe_load((HERE / "cases.yaml").read_text())}
    for path in sorted(RESULTS.glob("*.json")):
        rescore(path, cases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
