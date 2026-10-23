import type { ReactNode } from "react";

type Tone = "ok" | "warn" | "bad" | "info" | "neutral";

const TONES: Record<string, Tone> = {
  succeeded: "ok",
  verified: "ok",
  pass: "ok",
  answered: "ok",
  ok: "ok",
  covered: "ok",
  passed: "ok",
  processing: "info",
  queued: "neutral",
  running: "info",
  retrying: "warn",
  partial: "warn",
  rounding: "warn",
  conflicting: "warn",
  mismatch: "bad",
  failed: "bad",
  fail: "bad",
  error: "bad",
  unverified: "bad",
  not_found: "neutral",
  skipped: "neutral",
  missing: "neutral",
  reported: "neutral",
  calculated: "info",
  secondary: "neutral",
};

export function toneFor(status: string | null | undefined): Tone {
  return (status && TONES[status]) || "neutral";
}

export function StatusPill({
  status,
  label,
  tone,
  title,
}: {
  status: string | null | undefined;
  label?: string;
  tone?: Tone;
  title?: string;
}) {
  const t = tone ?? toneFor(status);
  const text = label ?? (status ? status.replace(/_/g, " ") : "unknown");
  return (
    <span className={`pill pill-${t}`} title={title}>
      {text}
    </span>
  );
}

export function Tag({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="tag" title={title}>
      {children}
    </span>
  );
}
