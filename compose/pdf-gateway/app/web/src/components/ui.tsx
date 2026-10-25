import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

// Small, dependency-free primitives in the shadcn/ui style. Text props are
// rendered as React text nodes only; nothing here accepts HTML.

export function cx(...classes: (string | false | null | undefined)[]) {
  return classes.filter(Boolean).join(" ");
}

export function Button({ variant = "primary", className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-slate-900 text-white hover:bg-slate-700 disabled:bg-slate-400",
    secondary: "bg-white text-slate-900 ring-1 ring-slate-300 hover:bg-slate-100 disabled:text-slate-400",
    danger: "bg-red-600 text-white hover:bg-red-500 disabled:bg-red-300",
    ghost: "text-slate-700 hover:bg-slate-200",
  }[variant];
  return <button {...props} className={cx("inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-900 disabled:cursor-not-allowed", styles, className)} />;
}

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-xl bg-white p-4 shadow-sm ring-1 ring-slate-200 sm:p-5", className)}>
      {(title || actions) && (
        <header className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

const tones: Record<string, string> = {
  good: "bg-green-100 text-green-800 ring-green-200",
  warn: "bg-amber-100 text-amber-900 ring-amber-200",
  bad: "bg-red-100 text-red-800 ring-red-200",
  info: "bg-sky-100 text-sky-900 ring-sky-200",
  muted: "bg-slate-100 text-slate-700 ring-slate-200",
};

export function Badge({ tone = "muted", children }: { tone?: keyof typeof tones; children: ReactNode }) {
  return <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1", tones[tone])}>{children}</span>;
}

const statusTone: Record<string, keyof typeof tones> = {
  READY: "good", REVIEW_REQUIRED: "warn", BLOCKED: "bad", FAILED: "bad", CANCELLED: "muted",
};

export const STATUS_LABEL: Record<string, string> = {
  RECEIVED: "Received", QUARANTINED: "Quarantined", QUEUED: "Queued", SCANNING: "Scanning", ANALYZING: "Checking signatures",
  AWAITING_POLICY: "Awaiting policy", SANITIZING: "Rebuilding (CDR)", VALIDATING_OUTPUT: "Validating output",
  ARCHIVING: "Archiving", READY: "Ready", REVIEW_REQUIRED: "Needs review", BLOCKED: "Blocked", FAILED: "Failed", CANCELLED: "Deleted",
};

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={statusTone[status] ?? "info"}>{STATUS_LABEL[status] ?? status}</Badge>;
}

export function Field({ label, hint, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">{label}</span>
      <input {...props} className="block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-base focus:border-slate-900 focus:outline-none sm:text-sm" />
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const text = error instanceof Error ? error.message : "Something went wrong";
  return <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800 ring-1 ring-red-200">{text}</p>;
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export const TERMINAL = new Set(["READY", "REVIEW_REQUIRED", "BLOCKED", "FAILED", "CANCELLED"]);
