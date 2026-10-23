# Baseline vs improved

- Baseline: `v1` with openrouter:deepseek/deepseek-v4-flash, run 2026-10-23T09:29:31+00:00
- Improved: `v2` with openrouter:deepseek/deepseek-v4-flash, run 2026-10-23T09:31:12+00:00 (documents from `baseline`, re-extracted: Annual Electricity Consumption.xlsx)

## Overall

| Metric | Baseline | Improved | Change |
|---|---|---|---|
| Cases passed | 5/16 | 10/16 | +5 |
| overall | 0.65 | 0.88 | +0.23 |
| answer | 0.64 | 0.89 | +0.24 |
| status | 0.44 | 0.75 | +0.31 |
| evidence | 0.78 | 0.99 | +0.20 |
| uncertainty | 0.57 | 1.00 | +0.43 |
| calculation | 0.50 | 0.70 | +0.20 |

## Repeat runs

The answer step is rerun on the same extracted documents to show how much of a difference is model noise.

| Pipeline | Runs | Passed per run | Mean passed | Overall per run | Mean overall |
|---|---|---|---|---|---|
| Baseline | 5 | 5, 8, 6, 6, 5 | 6.0/16 | 0.65, 0.70, 0.68, 0.68, 0.64 | 0.67 |
| Improved | 5 | 10, 11, 9, 9, 12 | 10.2/16 | 0.88, 0.86, 0.88, 0.86, 0.89 | 0.87 |

## Where the difference comes from

| Cases | Count | Baseline pass rate | Improved pass rate |
|---|---|---|---|
| Evidence includes a re-extracted document | 6 | 9/30 (30%) | 24/30 (80%) |
| Identical evidence in both pipelines (noise control) | 10 | 21/50 (42%) | 27/50 (54%) |

## Per case

| Case | Touched by change | Baseline | Improved | Change | Status (base -> improved) | Passes across runs (base / improved) |
|---|---|---|---|---|---|---|
| C01 Electricity per bill and billing period | no | FAIL 0.79 | FAIL 0.79 | +0.00 | partial -> partial | 0/5 / 2/5 |
| C02 Water consumption vs meter readings | no | PASS 0.84 | FAIL 0.69 | -0.15 | answered -> not_found | 2/5 / 2/5 |
| C03 Total hazardous waste recorded | no | FAIL 0.71 | PASS 1.00 | +0.29 | partial -> partial | 3/5 / 4/5 |
| C04 Workbook monthly values vs annual summaries | yes | FAIL 0.00 | PASS 1.00 | +1.00 | not_found -> conflicting | 0/5 / 5/5 |
| C05 Complaint-resolution deadline in the policy (absent) | no | PASS 1.00 | PASS 1.00 | +0.00 | not_found -> not_found | 5/5 / 5/5 |
| C06 Distinguishing amounts on the Adani bill | no | FAIL 0.67 | PASS 1.00 | +0.33 | partial -> answered | 1/5 / 4/5 |
| C07 FY 2024-25 electricity across bills (two organisations, two months) | yes | PASS 0.87 | PASS 0.90 | +0.03 | partial -> partial | 5/5 / 5/5 |
| C08 FY 2025-26 electricity from the calendar-year workbook | yes | FAIL 0.00 | PASS 1.00 | +1.00 | not_found -> partial | 0/5 / 4/5 |
| C09 FY 2026-27 electricity (9 of 12 months) | yes | FAIL 0.00 | PASS 0.97 | +0.97 | not_found -> partial | 0/5 / 5/5 |
| C10 FY 2025-26 water (single bill) | yes | PASS 0.90 | PASS 0.90 | +0.00 | partial -> partial | 4/5 / 5/5 |
| C11 BESCOM recorded vs billing demand | no | FAIL 0.83 | FAIL 0.83 | +0.00 | partial -> conflicting | 2/5 / 0/5 |
| C12 Waste in a financial year with no records | yes | FAIL 0.49 | FAIL 0.49 | +0.00 | not_found -> not_found | 0/5 / 0/5 |
| C13 Inconsistencies and gaps in the waste record | no | PASS 1.00 | PASS 1.00 | +0.00 | conflicting -> conflicting | 5/5 / 5/5 |
| C14 Water bill line items add up | no | FAIL 0.78 | PASS 1.00 | +0.22 | conflicting -> answered | 3/5 / 5/5 |
| C15 Adani consumption vs meter readings | no | FAIL 0.67 | FAIL 0.71 | +0.04 | not_found -> answered | 0/5 / 0/5 |
| C16 Policy scope, approval and commitments | no | FAIL 0.79 | FAIL 0.79 | +0.00 | partial -> partial | 0/5 / 0/5 |
