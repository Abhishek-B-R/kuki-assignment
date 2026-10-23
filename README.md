# KuKi sustainability data assistant

Upload utility bills, consumption workbooks, waste records and policies; the system extracts facts with
page/cell provenance, checks the arithmetic itself, totals Indian financial years with explicit coverage,
and answers questions with citations that are verified against the documents.

- **Backend:** Python 3.12, FastAPI, SQLite (WAL) as both the store and a durable job queue, a separate
  worker process, PyMuPDF and openpyxl for parsing. The model sits behind a small provider interface
  (`app/llm/`): OpenRouter (`deepseek/deepseek-v4-flash`, used for the saved eval runs) or Gemini
  (`gemini-3.5-flash`).
- **Dashboard:** React + TypeScript (Vite). It shows only live backend data.
- **Evals:** 16 hand-verified cases, a deterministic scorer, and saved baseline and improved runs.

## Setup

Requirements: [uv](https://docs.astral.sh/uv/), Node 20+, and an OpenRouter or Gemini API key.

```bash
cp .env.example .env          # set LLM_PROVIDER and that provider's key
make setup                    # uv sync + npm ci
make dev                      # API :8000, worker, dashboard :5173
```

Open http://localhost:5173 and upload the files in `dataset/`. To serve everything from one port,
run `make build`, then `make api` and `make worker` and open http://localhost:8000.

Free-tier Gemini keys allow about 5 requests a minute. Set `GEMINI_RPM=4` in `.env` and the client spaces
its calls out. Rate-limited jobs wait as long as the API asks, and that wait does not count as a failed
attempt.

```bash
make test                     # backend test suite (no network, model faked)
make eval-baseline            # ingest dataset/ with v1 extraction + run the 16 cases
make eval-improved            # reuse the baseline documents, re-extract the workbook with v2, rerun the cases
make compare                  # backend/evals/results/comparison.md
```

## Architecture

```
upload ─► documents + jobs (SQLite) ─► worker threads ─► parse ─► extract (LLM)    ─► verify ─► checks ─► save
                                         ▲   lease/heartbeat, retry, dedupe                           │
dashboard ◄─ FastAPI ◄─ facts / checks / pages / cells / events ◄────────────────────────────────────┘
               └─ /api/ask: evidence pack ─► LLM ─► citation + arithmetic verification
               └─ /api/fy:  deterministic FY selection, totals, coverage
```

### 1. Upload and background processing (`app/api.py`, `app/jobqueue.py`, `app/worker.py`)

- **Multiple files in one request.** Each file is streamed to disk while it is hashed (SHA-256), and
  size limits are enforced. Unsupported types get a `failed` document right away with the reason. They
  are not rejected silently.
- **Duplicates.** Content hashes are unique, so uploading the same bytes again returns the existing
  document (`duplicate: true`) without reprocessing it. Re-uploading a *failed* file is treated as a retry.
  A partial unique index allows only one queued or running job per document, so double clicks and
  concurrent uploads cannot process a document twice.
- **Leases, not locks.** A worker claims a job atomically (`BEGIN IMMEDIATE`) with a lease that a
  heartbeat thread keeps renewing. If a worker crashes or is killed, the lease expires and another worker
  takes the job over; a test simulates exactly this. Every write after the claim checks that the worker
  still owns the job, so a revived stale worker cannot overwrite a newer attempt.
- **Failure handling.**
  - *Transient* errors (timeouts, 5xx, malformed model JSON) retry with exponential backoff and jitter,
    up to `JOB_MAX_ATTEMPTS`.
  - *Rate limits* (429) are deferred for the time the provider asks. The deferral does not count as an
    attempt, and deferrals are capped.
  - *Permanent* errors (corrupt PDF, unsupported type, 4xx) fail at once.
  - Each document fails on its own, without affecting the others in the batch.
- **Idempotent results.** A document's facts and checks are replaced in a single transaction, so a retry
  or reprocess never leaves duplicates or half-written results behind.
- **Restart-safe.** All state lives in `data/kuki.db` (documents, jobs, parsed pages and cells, facts,
  checks, and a per-document event timeline). Restarting the API or worker loses nothing, and
  interrupted jobs resume.

### 2. Extraction with provenance (`app/pipeline.py`, `app/prompts.py`, `app/verify.py`, `app/checks.py`)

- **Parsing is deterministic.**
  - PDFs: the text layer for each page, word boxes and page renders. Pages with almost no text are
    flagged so the model reads them from the image.
  - Spreadsheets: every cell with its sheet and coordinate.
- **One model call per document.** Gemini receives the original PDF (so it sees the layout) plus the text
  layer, with each page marked; text-only models such as DeepSeek get the text layer alone. It returns schema-constrained JSON (`app/schemas.py`):
  - document type, organisation, issuer and reporting periods
  - typed facts, each with a page and verbatim quote, or a sheet and cell range
  - `relations`, the arithmetic the document implies (for example, "line items sum to the bill amount" or
    "(present − previous) × MF = units")
  - `gaps` and `anomalies`
- **Every fact is verified against the source.** For PDFs, the quote must appear on the cited page and
  the reported number must appear in the quote; boxes from the text layer then highlight it in the
  viewer. For sheets, the cited cell must hold the value. Each fact is tagged `verified`, `partial`
  (layout-split or wrong page, with the page corrected) or `unverified`.
- **Reported vs calculated.** The model never does arithmetic during extraction. All extracted facts are
  `origin: reported`. Our code evaluates the declared relations, and checks every TOTAL/ANNUAL row in a
  spreadsheet against the rows above it, skipping percentage, rate and subtotal columns. Each check
  stores the expression, the stated value and the difference, classified as `pass`, `rounding` (within
  the printed precision) or `fail`.
  - Two common misreadings are handled in code rather than reported as document errors:
    - A meter relation declared as present × previous × factor is checked as (present − previous) × factor.
    - An amount printed as a multiple of 10 (a bill's "round sum payable") that is within ₹10 of the
      computed value counts as `rounding`.
  - Both cases leave a note on the check.

### 3. Evidence-based answers (`app/qa.py`)

1. **Evidence pack.** For the selected documents the pack contains:
   - metadata, gaps and anomalies
   - every fact with its source and evidence status
   - our arithmetic checks
   - the FY report when the question names a financial year
   - the most relevant page text
2. **Structured answer.** The model returns a status (`answered`, `partial`, `not_found` or
   `conflicting`), the answer, key values tagged reported or calculated, calculations as plain
   expressions, citations, gaps, inconsistencies and assumptions.
3. **Post-checks.** Each citation is checked against the stored page text or cells, and each calculation
   is recomputed with a safe arithmetic evaluator. The UI shows how many of each were confirmed.

### 4. Financial years (`app/fy.py`)

The FY engine is deterministic and the model only narrates its output.

- **Parsing.** It reads `FY 2024-25`, `FY24-25`, `2024-25`, `FY25` and `FY2025`. The single-year forms are
  taken as the year ending in March, and the answer states that assumption.
- **What is counted.**
  - Only primary facts with a metric and a period count. History charts and previous-month figures are
    listed as excluded.
  - Groups are formed by organisation, metric, dimension and unit, and different groups are never added
    together.
  - Within a group the finest-grained records win. Quarterly or annual totals that overlap monthly
    records are excluded, so nothing is counted twice, but they are still compared with the sum of their
    months.
  - Periods that straddle the year boundary are prorated by days and flagged.
  - Rates, shares, ratios, capacities and statistics are excluded with a reason, because adding or
    prorating them is meaningless. This covers units such as %, INR/kWh, kWp and kVA, and non-money labels
    such as "average" or "highest". Lakh and crore amounts are converted to INR.
- **Coverage.** Each series reports covered days and a month-by-month strip (covered, partial or
  missing). Notes flag mixed organisations, incomplete coverage, prorating and unverified records.

## Evaluation

Full report: [`backend/evals/results/comparison.md`](backend/evals/results/comparison.md). Raw answers and scores
for every run are in `backend/evals/results/*.json`, and the dashboard's Evaluation tab shows them side by side.

**Cases.** There are 16 cases in `backend/evals/cases.yaml`, each with an expected answer checked by hand against the
documents. They cover:
- per-bill values and billing periods
- meter-reading arithmetic and line items that should sum to the bill total
- totals that disagree with their parts in the workbook
- Indian financial-year totals with partial coverage, including a FY with no records
- a mix of two organisations' bills
- reported vs calculated amounts
- questions whose answer is absent from the documents

**Scoring.** `evals/score.py` is deterministic: it involves no LLM judge. It scores five dimensions:
- answer (35%): the required values or phrases appear in the answer
- status (15%)
- evidence (20%): the expected page or cell is cited, and the citation verified
- uncertainty (20%): the gaps and conflicts that must be flagged are flagged
- calculation (10%): the stated arithmetic re-computes

A case passes at 0.80 overall, provided its status is right and it makes no claim the case forbids.

**Model.** The saved runs use `deepseek/deepseek-v4-flash` through OpenRouter. The first baseline was run on a
free-tier Gemini key (20 requests a day). It got through 14 of 16 cases (on earlier code) before the daily cap and is kept as
`gemini-baseline.partial.json`.

### Weakness found in the baseline

v1 asks the model to transcribe every fact it finds, including every monthly cell of the electricity workbook.
That breaks on the one large table in the dataset, in a different way per model:
- **DeepSeek:** the output ran past the 32k-token limit. The workbook failed after 4 attempts and every workbook
  question came back `not_found`.
- **Gemini:** the output fit only because the model skipped the monthly rows and returned 9 annual facts. The FY
  engine then had to prorate the calendar-year total, so FY 2025-26 came out as 597,976 kWh instead of
  584,323 kWh (Jan to Mar 2026), and Q1's 585,323 vs 584,323 disagreement went unseen.

### The change (v2)

For spreadsheets, the model now describes each table instead of copying it. For each table it gives:
- the label column and value column
- the first and last row
- the metric, unit and year
- whether quarters are calendar or Indian-FY quarters

`app/series.py` then reads every row from the cells and creates one fact per row. Each fact cites its own cell
and has its period parsed from the row label. The model still lists totals and the arithmetic relations, so the
checks still run.

Output size no longer grows with the table: the workbook went from failing to 335 cell-cited facts. Nothing else
changed. The same documents are reused (`--reuse-db baseline`), only the workbook is re-extracted, and the
answer step and prompts are identical.

### Results

| | Baseline (v1) | Improved (v2) |
|---|---|---|
| Cases passed, main run | 5/16 | 10/16 |
| Cases passed, 5 runs | 5, 8, 6, 6, 5 (mean 6.0) | 10, 11, 9, 9, 12 (mean 10.2) |
| Mean overall score, 5 runs | 0.67 | 0.87 |

The answer step is not deterministic, even at temperature 0, because OpenRouter routes calls across hosts. One
run per pipeline cannot tell an effect from noise, so the answer step was run five times on each pipeline's
extracted documents (`make eval-repeats`). Cases whose evidence does not include the workbook get byte-identical
model input in both pipelines (checked), which makes them a noise control:

| Cases | Baseline pass rate | Improved pass rate | Gap |
|---|---|---|---|
| Evidence includes the re-extracted workbook (6) | 30% (9/30) | 80% (24/30) | +50 points |
| Identical evidence in both pipelines (10) | 42% (21/50) | 54% (27/50) | +12 points, all noise |

The control group's gap shows how large pure noise is on this suite. Across the three full rounds of runs made
while fixing the bugs below, it was −2, −4 and +12 points, while the touched cases' gap was +56, +50 and +50.
A single run per pipeline would not have shown this. For example, C06 has the same input in both pipelines and
passed 1/5 vs 4/5.

**What improved:**
- **C04 and C09 went from 0/5 to 5/5, and C08 from 0/5 to 4/5.** C04 is the workbook totals that disagree; C08 is
  FY 2025-26 = 584,323 kWh, with 3 of 12 months covered; C09 is FY 2026-27 = 1,841,800 kWh, with 9 of 12 months
  covered.
- **Uncertainty went from 0.57 to 1.00 in the main run.** Workbook answers now say which months are missing and
  flag the Q1 conflict (585,323 stated vs 584,323 summed).
- **Evidence went from 0.78 to 0.99.** Workbook answers now cite the actual monthly cells.

**What regressed:**
- Nothing the change touched got worse: C07 is 5/5 in both, and C10 is 4/5 vs 5/5.
- In the main run, C02 went from pass to fail, but it has identical input in both pipelines and is 2/5 vs 2/5
  across runs.
- C11 is 2/5 vs 0/5 on identical input, also noise. It is one of the unstable cases listed below.

**Fixed along the way, in both pipelines:** reading the live dashboard turned up three bugs in deterministic
code. They were fixed with tests, the stored checks were recomputed offline, and all ten runs were re-answered,
so both columns reflect the final code.
- **The FY engine added up percentages, per-unit rates and capacities.** For example, it reported
  66% + 65.2% + 63.7% = 194.9% and prorated a 250 kWp capacity. These are now excluded with a reason.
- **Two arithmetic misreadings were reported as document errors:**
  - a meter relation declared as present × previous × factor
  - a bill's "round sum payable" amounts, which are rounded to ₹10
  On the Adani bill those were 3 false failures; it now has none.
- **The scorer did not match typographic dashes.**
These fixes raised both pipelines alike.

**What remains:**
- **C12 (absence answers do not say why)** is at 0/10. Asked about waste in FY 2023-24, the model correctly
  says there is none, but it never mentions that the only waste record is from March 2023 (FY 2022-23), even
  though the FY report lists it under "documents with data only in other financial years".
- **Status calibration.** C16 is at 0/10 and C01 at 2/10: the answers are complete and cited but labelled
  `partial`. C11 flips between `answered`, `partial` and `conflicting`.
- **C15 (meter check)** is at 0/10.
  - Nine of the ten answers contain the correct, re-checked calculation (4,132.27 − 3,539.68) × 80 = 47,407.2.
  - In eight of those, the readings appear only in the calculation, not in the prose or key values the scorer
    reads. That part is the scorer being strict, and it was deliberately not loosened after seeing the results.
  - The rest is real: four answers were labelled `partial`, and one baseline answer said `not_found`.
- **Answer stability.** Single-document cases flip between runs. A prompt change aimed at status and absence
  explanations, plus pinning one OpenRouter provider, would be the next experiment, measured the same way.

```bash
make eval-baseline && make eval-improved   # main runs
make eval-repeats                          # answer step 4 more times on each pipeline's documents
make compare                               # comparison.md
cd backend && uv run python -m evals.rescore   # re-score saved runs after a scorer change (no model calls)
```

`evals.run --name <run> --answers-only` re-answers a finished run on its existing documents, without
extracting anything again.

## Design choices

- **SQLite for the queue.** It needs no extra infrastructure, it is transactional, and it survives
  restarts. The queue semantics (leases, ownership checks, one live job per document, backoff) would map
  directly onto Postgres `SKIP LOCKED` or a broker if several machines had to share the work.
- **The model reads and proposes; code calculates and verifies.** LLM arithmetic is the most common
  source of confident wrong numbers, so every number we compute is computed in Python and labelled
  `calculated`.
- **No answers are hardcoded.** Prompts are generic, and nothing in the code refers to the sample
  documents. The FY engine, checks and verification work on any document that yields facts.

## Known limitations

- **Text-only models never see the page image.** With DeepSeek, a scanned page without a text layer has nothing
  to extract; Gemini receives the PDF itself. Pages with little text are flagged in the extraction input
  either way.
- **Spreadsheet series depend on the model describing the table correctly.** Values always come from the cells,
  but a wrong year or quarter basis would mislabel the periods. Problems found while expanding the rows are
  recorded as gaps on the document.
- **One machine.** SQLite and in-process worker threads suit a single host. Several hosts would need Postgres
  (`SKIP LOCKED`) or a broker. The queue's semantics would carry over unchanged.
- **The eval set is small:** 16 cases over 6 documents, written and checked by one person. Matching is by
  required values and phrases, so a correct answer worded unusually can lose points. Repeat runs make the noise
  visible, but five runs is still a small sample per case.
- **No authentication or multi-tenancy.** Every uploaded document is visible to every user of the instance.
- **Free-tier Gemini keys** allow about 20 requests a day. A full eval (about 40 calls) then takes several days;
  the runner resumes where it stopped.

## Repository layout

```
backend/app/        API, queue, worker, pipeline, parsing, verification, checks, FY engine, Q&A
backend/tests/      unit + end-to-end tests (model faked)
backend/evals/      cases.yaml, runner, scorer, results/ (baseline*.json, improved*.json, comparison.md)
frontend/           dashboard
dataset/            the six assignment documents
```
