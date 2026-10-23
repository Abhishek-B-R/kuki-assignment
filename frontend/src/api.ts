// Typed helpers for the FastAPI backend. Shapes mirror backend/app/api.py, qa.py, fy.py and evals/.

export type DocStatus = "queued" | "processing" | "retrying" | "succeeded" | "failed";
export type Evidence = "verified" | "partial" | "unverified";

export interface Period {
  label: string;
  start: string | null;
  end: string | null;
}

export interface Anomaly {
  description: string;
  page: number | null;
  sheet: string | null;
  cell_range: string | null;
  quote: string | null;
}

export interface DocumentRow {
  id: string;
  filename: string;
  sha256: string;
  mime: string;
  size_bytes: number;
  status: DocStatus;
  stage: string | null;
  error: string | null;
  attempts: number;
  doc_type: string | null;
  title: string | null;
  organisation: string | null;
  issuer: string | null;
  summary: string | null;
  periods: Period[] | null;
  gaps: string[] | null;
  anomalies: Anomaly[] | null;
  page_count: number | null;
  pipeline_version: string | null;
  created_at: number;
  updated_at: number;
  processed_at: number | null;
  // Present on the list endpoint only.
  fact_count?: number;
  verified_count?: number;
  failed_checks?: number;
}

export interface DocumentsResponse {
  documents: DocumentRow[];
  jobs: Record<string, number>;
}

export interface UploadResult {
  document?: DocumentRow;
  duplicate: boolean;
  requeued?: boolean;
  filename?: string;
  error?: string;
}

export interface Fact {
  id: string;
  document_id: string;
  local_id: string | null;
  key: string;
  label: string | null;
  category: string;
  metric: string | null;
  dimension: string | null;
  is_primary: number;
  value_text: string | null;
  value_num: number | null;
  unit: string | null;
  period_start: string | null;
  period_end: string | null;
  origin: string;
  page: number | null;
  sheet: string | null;
  cell_range: string | null;
  quote: string | null;
  evidence_status: Evidence;
  bbox: number[][] | null;
  notes: string | null;
  filename: string;
  organisation: string | null;
  doc_type: string | null;
}

export interface Check {
  id: string;
  document_id: string;
  name: string;
  status: "pass" | "rounding" | "fail" | "skipped";
  expected: number | null;
  actual: number | null;
  difference: number | null;
  source: string;
  details: {
    expression?: string;
    stated?: string;
    target_ref?: string[];
    operand_refs?: string[];
    reason?: string;
    target?: string;
    operands?: string[];
  } | null;
}

export interface EventRow {
  ts: number;
  level: string;
  message: string;
}

export interface DocumentDetailResponse {
  document: DocumentRow;
  facts: Fact[];
  checks: Check[];
  events: EventRow[];
  sheets: string[];
}

export interface SheetCell {
  ref: string;
  row: number;
  col: number;
  value: string | null;
  formula: string | null;
}

export interface SheetResponse {
  sheet: string;
  rows: number;
  cols: number;
  cells: SheetCell[];
}

// ---- financial year ---------------------------------------------------------------------

export interface FySource {
  fact_id: string;
  document_id: string;
  filename: string;
  page: number | null;
  sheet: string | null;
  cell_range: string | null;
  quote: string | null;
  evidence_status: Evidence | null;
}

export interface FyBrief {
  label: string | null;
  value: number;
  unit: string | null;
  period_start: string;
  period_end: string;
  dimension: string | null;
  source: FySource;
}

export interface FyRecord extends FyBrief {
  included_days: number;
  share: number;
  contribution: number;
  prorated: boolean;
}

export interface FyMonth {
  month: string;
  covered_days: number;
  days: number;
  status: "covered" | "partial" | "missing";
}

export interface FyCrossCheck {
  description: string;
  stated: number;
  sum_of_parts: number;
  difference: number;
  status: "pass" | "fail";
  stated_ref: FySource;
}

export interface FyGroup {
  organisation: string;
  metric: string;
  dimension: string;
  unit: string | null;
  total: number;
  origin: string;
  calculation: string;
  records: FyRecord[];
  coverage: {
    covered_days: number;
    fy_days: number;
    ratio: number;
    months_covered: number;
    months_missing: string[];
    months_partial: string[];
    months: FyMonth[];
    complete: boolean;
  };
  includes_prorated: boolean;
  cross_checks: FyCrossCheck[];
  unverified_records: number;
}

export interface FyExcluded extends FyBrief {
  reason: string;
}

export interface NotReady {
  id: string;
  filename: string;
  status: string;
}

export interface FyReportData {
  financial_year: string;
  start: string;
  end: string;
  groups: FyGroup[];
  excluded: FyExcluded[];
  documents_outside_fy: Record<string, string[]>;
  organisations: string[];
  notes: string[];
  not_ready?: NotReady[];
}

// ---- ask --------------------------------------------------------------------------------

export type AnswerStatus = "answered" | "partial" | "not_found" | "conflicting";

export interface AnswerCitation {
  document_id: string | null;
  filename: string | null;
  page: number | null;
  sheet: string | null;
  cell_range: string | null;
  quote: string;
  fact_id: string | null;
  verification: Evidence;
  note: string | null;
  bbox?: number[][];
}

export interface KeyValue {
  label: string;
  value: string;
  unit: string | null;
  origin: "reported" | "calculated";
  citation_indexes: number[];
}

export interface CalculationResult {
  description: string;
  expression: string;
  result: number;
  unit: string | null;
  recomputed?: number | null;
  ok?: boolean;
}

export interface Support {
  citations?: number;
  verified?: number;
  partial?: number;
  unverified?: number;
  calculations_ok?: number;
  calculations?: number;
}

export interface AskResponse {
  question: string;
  financial_year: string | null;
  documents: { id: string; filename: string }[];
  not_ready: NotReady[];
  status: AnswerStatus | "error";
  answer: string;
  key_values: KeyValue[];
  calculations: CalculationResult[];
  citations: AnswerCitation[];
  gaps: string[];
  inconsistencies: string[];
  assumptions: string[];
  support: Support;
  fy_report: FyReportData | null;
  elapsed_s: number;
}

export interface AskRequest {
  question: string;
  document_ids?: string[];
  financial_year?: string;
}

// ---- evals ------------------------------------------------------------------------------

export interface EvalScore {
  scores: Record<string, number | null>;
  overall: number;
  passed: boolean;
  detail: {
    missing?: unknown[];
    violations?: string[];
    status?: string;
    evidence?: { sources_hit: number; sources: number; citations: number; supported: number };
    sources_missed?: Record<string, unknown>[];
    flags_missing?: unknown[];
    calculations?: { expression: string; result: number; ok?: boolean }[];
  };
}

export interface EvalCaseResult {
  id: string;
  title: string;
  question: string;
  answer: Partial<AskResponse> & { status?: string; answer?: string };
  score: EvalScore;
}

export interface EvalSummary {
  cases: number;
  passed: number;
  overall: number;
  [dim: string]: number | null;
}

export interface ExtractionSnapshotRow {
  filename: string;
  status: string;
  error: string | null;
  attempts: number;
  doc_type: string | null;
  organisation: string | null;
  facts: number;
  verified_facts: number;
  failed_checks: number;
}

export interface EvalRun {
  name: string;
  version: string;
  model: string;
  ran_at: string;
  reused_extraction_from: string | null;
  extraction: ExtractionSnapshotRow[];
  summary: EvalSummary;
  cases: EvalCaseResult[];
}

export interface EvalCaseDef {
  id: string;
  title: string;
  question: string;
  documents: string[] | "all";
  expected_answer?: string;
  expected_status?: string[];
  [k: string]: unknown;
}

export interface EvalRepeat {
  name: string;
  ran_at: string;
  summary: EvalSummary;
}

export interface EvalsResponse {
  runs: { baseline?: EvalRun; improved?: EvalRun };
  repeats?: { baseline?: EvalRepeat[]; improved?: EvalRepeat[] };
  cases?: EvalCaseDef[];
}

export interface Health {
  ok: boolean;
  jobs: Record<string, number>;
  model: string;
  extract_version: string;
}

// ---- fetch helpers ----------------------------------------------------------------------

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
      .join("; ");
  }
  return JSON.stringify(detail);
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch {
    throw new ApiError(0, "Cannot reach the API server");
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail !== undefined) msg = detailText(body.detail);
    } catch {
      // body was not JSON
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

const enc = encodeURIComponent;

function idsQuery(ids?: string[] | null): string {
  if (!ids || ids.length === 0) return "";
  return "?" + ids.map((i) => `document_ids=${enc(i)}`).join("&");
}

export const api = {
  health: () => request<Health>("/api/health"),
  documents: () => request<DocumentsResponse>("/api/documents"),
  document: (id: string) => request<DocumentDetailResponse>(`/api/documents/${enc(id)}`),
  upload: (files: File[]) => {
    const fd = new FormData();
    for (const f of files) fd.append("files", f, f.name);
    return request<UploadResult[]>("/api/documents", { method: "POST", body: fd });
  },
  reprocess: (id: string) =>
    request<{ document: DocumentRow }>(`/api/documents/${enc(id)}/reprocess`, { method: "POST" }),
  remove: (id: string) => request<{ deleted: string }>(`/api/documents/${enc(id)}`, { method: "DELETE" }),
  sheet: (id: string, sheet: string) => request<SheetResponse>(`/api/documents/${enc(id)}/sheets/${enc(sheet)}`),
  ask: (body: AskRequest) =>
    request<AskResponse>("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  fyYears: (ids?: string[] | null) => request<{ years: string[] }>(`/api/fy${idsQuery(ids)}`),
  fy: (label: string, ids?: string[] | null) =>
    request<FyReportData>(`/api/fy/${enc(label)}${idsQuery(ids)}`),
  evals: () => request<EvalsResponse>("/api/evals"),
};

export const fileUrl = (id: string) => `/api/documents/${enc(id)}/file`;
export const pageUrl = (id: string, page: number) => `/api/documents/${enc(id)}/pages/${page}.png`;

// ---- shared formatting ------------------------------------------------------------------

export function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return n.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

export function fmtTime(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return "-";
  return new Date(epochSeconds * 1000).toLocaleString();
}

export function fmtPeriod(start: string | null | undefined, end: string | null | undefined): string {
  if (!start && !end) return "";
  if (start && end && start !== end) return `${start} to ${end}`;
  return start || end || "";
}

export function errorMessage(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}
