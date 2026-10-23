# Baseline vs improved

- Baseline: `v1` with openrouter:deepseek/deepseek-v4-flash, run 2026-10-23T13:57:24+00:00
- Improved: `v2` with openrouter:deepseek/deepseek-v4-flash, run 2026-10-23T13:58:39+00:00 (documents from `baseline`, re-extracted: Annual Electricity Consumption.xlsx)

## Overall

| Metric | Baseline | Improved | Change |
|---|---|---|---|
| Cases passed | 5/16 | 8/16 | +3 |
| overall | 0.67 | 0.86 | +0.19 |
| answer | 0.67 | 0.87 | +0.20 |
| status | 0.50 | 0.69 | +0.19 |
| evidence | 0.75 | 0.91 | +0.16 |
| uncertainty | 0.57 | 1.00 | +0.43 |
| calculation | 0.71 | 1.00 | +0.29 |

## Repeat runs

The answer step is rerun on the same extracted documents to show how much of a difference is model noise.

| Pipeline | Runs | Passed per run | Mean passed | Overall per run | Mean overall |
|---|---|---|---|---|---|
| Baseline | 5 | 5, 7, 6, 6, 5 | 5.8/16 | 0.67, 0.72, 0.69, 0.69, 0.68 | 0.69 |
| Improved | 5 | 8, 10, 8, 9, 8 | 8.6/16 | 0.86, 0.89, 0.85, 0.89, 0.84 | 0.87 |

## Where the difference comes from

| Cases | Count | Baseline pass rate | Improved pass rate |
|---|---|---|---|
| Evidence includes a re-extracted document | 6 | 10/30 (33%) | 26/30 (87%) |
| Identical evidence in both pipelines (noise control) | 10 | 19/50 (38%) | 17/50 (34%) |

## Ablation: v1 with a larger output cap

v1 extraction of the workbook re-run with the 64k-token cap (the baseline ran with 32k, which cut the workbook off). This separates the series design from simply allowing longer output.

| Pipeline | Runs | Passed per run | Mean passed | Mean overall | Pass rate, cases touched by the workbook |
|---|---|---|---|---|---|
| Baseline (v1, 32k) | 5 | 5, 7, 6, 6, 5 | 5.8/16 | 0.69 | 10/30 (33%) |
| v1, 64k | 5 | 10, 11, 9, 9, 9 | 9.6/16 | 0.88 | 25/30 (83%) |
| Improved (v2) | 5 | 8, 10, 8, 9, 8 | 8.6/16 | 0.87 | 26/30 (87%) |

Workbook extraction: v1 at 64k produced 334 facts; v2 produced 335.

## Per case

| Case | Touched by change | Baseline | Improved | Change | Status (base -> improved) | Passes across runs (base / improved) |
|---|---|---|---|---|---|---|
| C01 Electricity per bill and billing period | no | FAIL 0.79 | FAIL 0.79 | +0.00 | partial -> partial | 0/5 / 0/5 |
| C02 Water consumption vs meter readings | no | PASS 0.98 | FAIL 0.79 | -0.19 | answered -> not_found | 5/5 / 3/5 |
| C03 Total hazardous waste recorded | no | FAIL 0.71 | FAIL 0.71 | +0.00 | partial -> partial | 1/5 / 1/5 |
| C04 Workbook monthly values vs annual summaries | yes | FAIL 0.00 | PASS 1.00 | +1.00 | not_found -> conflicting | 0/5 / 5/5 |
| C05 Complaint-resolution deadline in the policy (absent) | no | FAIL 0.71 | FAIL 0.71 | +0.00 | not_found -> not_found | 1/5 / 1/5 |
| C06 Distinguishing amounts on the Adani bill | no | FAIL 0.67 | FAIL 0.67 | +0.00 | partial -> partial | 0/5 / 0/5 |
| C07 FY 2024-25 electricity across bills (two organisations, two months) | yes | PASS 1.00 | PASS 0.98 | -0.02 | partial -> partial | 5/5 / 5/5 |
| C08 FY 2025-26 electricity from the calendar-year workbook | yes | FAIL 0.06 | PASS 1.00 | +0.94 | not_found -> partial | 0/5 / 5/5 |
| C09 FY 2026-27 electricity (9 of 12 months) | yes | FAIL 0.00 | PASS 0.99 | +0.99 | not_found -> partial | 0/5 / 5/5 |
| C10 FY 2025-26 water (single bill) | yes | PASS 1.00 | PASS 0.97 | -0.03 | partial -> partial | 5/5 / 5/5 |
| C11 BESCOM recorded vs billing demand | no | FAIL 0.75 | FAIL 0.83 | +0.08 | partial -> partial | 1/5 / 1/5 |
| C12 Waste in a financial year with no records | yes | FAIL 0.46 | FAIL 0.48 | +0.03 | not_found -> not_found | 0/5 / 1/5 |
| C13 Inconsistencies and gaps in the waste record | no | PASS 1.00 | PASS 1.00 | +0.00 | conflicting -> partial | 5/5 / 5/5 |
| C14 Water bill line items add up | no | PASS 0.97 | PASS 1.00 | +0.03 | answered -> answered | 5/5 / 5/5 |
| C15 Adani consumption vs meter readings | no | FAIL 0.81 | PASS 1.00 | +0.19 | partial -> answered | 1/5 / 1/5 |
| C16 Policy scope, approval and commitments | no | FAIL 0.79 | FAIL 0.79 | +0.00 | partial -> partial | 0/5 / 0/5 |
