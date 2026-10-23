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
The output therefore grows with the size of the table, and that broke on the one large table in the dataset:
- **DeepSeek:** the output ran past the 32k-token cap the client then used. The workbook failed after 4 attempts
  and every workbook question came back `not_found`.
- **Gemini:** the output fit only because the model skipped the monthly rows and returned 9 annual facts. The FY
  engine then had to prorate the calendar-year total, so FY 2025-26 came out as 597,976 kWh instead of
  584,323 kWh (Jan to Mar 2026).

### The change (v2)

For spreadsheets, the model now describes each table instead of copying it. For each table it gives:
- the label column and value column
- the first and last row
- the metric, unit and year
- whether quarters are calendar or Indian-FY quarters

`app/series.py` then reads every row from the cells and creates one fact per row. Each fact cites its own cell
and has its period parsed from the row label. The model still lists totals and the arithmetic relations, so the
checks still run.

Nothing else changes between the two pipelines. The improved run reuses the baseline's documents
(`--reuse-db baseline`) and re-extracts only the workbook.

### Results

| | Baseline (v1) | Improved (v2) |
|---|---|---|
| Cases passed, main run | 5/16 | 8/16 |
| Cases passed, 5 runs | 5, 7, 6, 6, 5 (mean 5.8) | 8, 10, 8, 9, 8 (mean 8.6) |
| Mean overall score, 5 runs | 0.69 | 0.87 |

The answer step is not deterministic, even at temperature 0. One run per pipeline cannot tell an effect from
noise, so the answer step was run five times on each pipeline's extracted documents (`make eval-repeats`). Cases
whose evidence does not include the workbook get byte-identical model input in both pipelines (checked), which
makes them a noise control:

| Cases | Baseline pass rate | Improved pass rate |
|---|---|---|
| Evidence includes the re-extracted workbook (6) | 33% (10/30) | 87% (26/30) |
| Identical evidence in both pipelines (10) | 38% (19/50) | 34% (17/50) |

**What improved:**
- **C04, C08 and C09 went from 0/5 to 5/5.** C04 is the workbook totals that disagree; C08 is FY 2025-26 = 584,323
  kWh, with 3 of 12 months covered, plus the Q1 conflict (585,323 stated vs 584,323 summed); C09 is FY 2026-27 =
  1,841,800 kWh, with 9 of 12 months covered.
- **In the main run:** uncertainty went from 0.57 to 1.00, evidence from 0.75 to 0.91, and calculation from 0.71
  to 1.00.

**What regressed:**
- No case the change touched got worse: C07 and C10 are 5/5 in both.
- C02 went from 5/5 to 3/5 with identical input, which is noise. The control group as a whole is flat
  (38% vs 34%).

### Ablation: is it the design, or just a bigger output cap?

The baseline's DeepSeek failure was partly caused by the 32k cap, so v1 was re-run with a 64k cap
(`v1-64k*.json`):

| Pipeline (5 runs each) | Mean passed | Mean overall | Workbook cases passed |
|---|---|---|---|
| Baseline: v1, 32k cap | 5.8/16 | 0.69 | 33% |
| v1, 64k cap | 9.6/16 | 0.88 | 83% |
| v2, 64k cap | 8.6/16 | 0.87 | 87% |

On this dataset, **raising the cap recovers about as much of the score as v2 does**, and the two are within noise
of each other. So the honest claim for v2 is not a higher score. It is that the extraction no longer depends on
the model transcribing a whole table. Measured on the workbook with DeepSeek:

| | v1 | v2 |
|---|---|---|
| Output tokens per call | 33k to 59k | 16k to 32k |
| Time per call | 5 to 10 min | 2 to 5 min |
| Monthly facts | 119, 211 or 223 in three runs; one run silently missed about half the rows | 204 to 209; every declared column is read in full from the cells |
| Fit within a 32k cap? | No | Yes |
| Larger tables | grow in step with the table | stay about the same |

A bigger table than this one would push v1 past any cap, and an incomplete transcription is silent: the FY
totals would simply be wrong. v2's facts per column come from code, so a column is either read completely or
reported as a gap.

**Fixed along the way, in both pipelines.** Each fix came with tests, and all runs were re-answered on the final
code, so every column reflects the same code.
- **FY engine:** it added up and prorated non-additive values (it reported 66% + 65.2% + 63.7% = 194.9% and
  prorated a 250 kWp capacity). Rates, shares, ratios, capacities and statistics are now excluded from totals,
  and lakh, crore and `cubic_meters` units are normalised.
- **Arithmetic checks:** a meter relation declared as present × previous × factor is now checked as
  (present − previous) × factor, and "round sum payable" amounts within ₹10 count as rounding. On the Adani bill
  that removed 3 false failures.
- **Answers:** the answer step now stays inside the requested financial year. Data from other years is mentioned
  only as outside it.
- **OpenRouter hosting:**
  - One host (Baidu) returned corrupted text, such as `"savngs"`, missing quotes and missing commas, and another
    ran past 60k tokens. Calls now prefer Parasail, then DeepInfra, and skip those two
    (`OPENROUTER_PROVIDERS` / `OPENROUTER_IGNORE_PROVIDERS`).
  - Small JSON syntax slips are repaired before strict schema validation.
  - The output cap is 64k.
- **Schema:** a null `quarter_basis` (for tables without quarters) no longer rejects a whole extraction.
- **Scorer:** it now matches typographic dashes.

A fresh end-to-end run on the final code exercised the whole processing path:
- 6 documents plus a duplicate and a corrupt file uploaded together
- the worker killed with `kill -9` mid-extraction, its jobs reclaimed after the lease expired, and the API and
  worker restarted
- the result: all 6 documents processed (the workbook with 323 cell-cited facts), the duplicate skipped, and the
  corrupt file failed on its own

**What remains:**
- **C12 (absence answers do not say why)** is at 1/10. Asked about waste in FY 2023-24, the model correctly
  says there is none but rarely mentions that the only waste record is from March 2023 (FY 2022-23).
- **Status calibration.** C01, C06 and C16 are complete, cited answers labelled `partial` (0/10 each). C11 flips
  between statuses.
- **Absence citations (C05).** The model cites the right policy page for "no deadline is specified", but its quote
  does not match the page text, so the citation is unverified.
- **C03 and C15.** The answers are right, but the prose leaves out details the case requires: the waste types in
  C03, and in C15 the meter readings, which appear only in the verified calculation. This is partly the scorer
  being strict, and it was deliberately not loosened after seeing the results.
- **Next experiment:** a prompt change aimed at status labels and absence explanations, measured the same way
  against the control group.

```bash
make eval-baseline && make eval-improved   # main runs
make eval-repeats                          # answer step 4 more times on each pipeline's documents
make eval-ablation                         # v1 re-extracted with the 64k cap, 5 answer runs
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
- **Model hosting varies.** Output quality depends on which OpenRouter host serves a call; the defaults pin
  hosts that were measured to work, but hosts change.
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
