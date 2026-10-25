import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from "react";

// Small, dependency-free primitives in the shadcn/ui style. Text props are
// rendered as React text nodes only; nothing here accepts HTML.

export function cx(...classes: (string | false | null | undefined)[]) {
  return classes.filter(Boolean).join(" ");
}

export function Button({ variant = "primary", className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost" }) {
  const styles = {
    primary: "bg-slate-900 text-white hover:bg-slate-700 disabled:bg-slate-400 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white dark:disabled:bg-slate-600",
    secondary: "bg-white text-slate-900 ring-1 ring-slate-300 hover:bg-slate-100 disabled:text-slate-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700 dark:hover:bg-slate-800",
    danger: "bg-red-600 text-white hover:bg-red-500 disabled:bg-red-300",
    ghost: "text-slate-700 hover:bg-slate-200 dark:text-slate-300 dark:hover:bg-slate-800",
  }[variant];
  return <button {...props} className={cx("inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-500 disabled:cursor-not-allowed", styles, className)} />;
}

export const linkButton = "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-medium transition";
export const primaryLink = cx(linkButton, "bg-slate-900 text-white hover:bg-slate-700 dark:bg-slate-100 dark:text-slate-900 dark:hover:bg-white");
export const secondaryLink = cx(linkButton, "ring-1 ring-slate-300 hover:bg-slate-100 dark:ring-slate-700 dark:hover:bg-slate-800");

export function Card({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cx("rounded-xl bg-white p-4 shadow-sm ring-1 ring-slate-200 sm:p-5 dark:bg-slate-900 dark:shadow-none dark:ring-slate-800", className)}>
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

const tones = {
  good: "bg-green-100 text-green-800 ring-green-200 dark:bg-green-950 dark:text-green-300 dark:ring-green-900",
  warn: "bg-amber-100 text-amber-900 ring-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:ring-amber-900",
  bad: "bg-red-100 text-red-800 ring-red-200 dark:bg-red-950 dark:text-red-300 dark:ring-red-900",
  info: "bg-sky-100 text-sky-900 ring-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:ring-sky-900",
  muted: "bg-slate-100 text-slate-700 ring-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700",
};
export type Tone = keyof typeof tones;

export function Badge({ tone = "muted", children }: { tone?: Tone; children: ReactNode }) {
  return <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1", tones[tone])}>{children}</span>;
}

/** A coloured notice box: what happened, in words. */
export function Notice({ tone, title, children }: { tone: Tone; title: ReactNode; children?: ReactNode }) {
  return (
    <div className={cx("rounded-xl p-4 ring-1", tones[tone])} data-testid="verdict">
      <p className="text-base font-semibold">{title}</p>
      {children && <div className="mt-1 text-sm opacity-90">{children}</div>}
    </div>
  );
}

export function Spinner() {
  return <span aria-hidden className="inline-block size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />;
}

export type StepState = "done" | "running" | "waiting" | "failed" | "skipped";

/** A vertical list of steps, each with a state mark and a plain sentence. */
export function Steps({ items }: { items: { key: string; label: ReactNode; detail?: ReactNode; state: StepState }[] }) {
  const mark: Record<StepState, ReactNode> = {
    done: <span className="flex size-6 items-center justify-center rounded-full bg-green-600 text-xs text-white">✓</span>,
    running: <span className="flex size-6 items-center justify-center rounded-full bg-sky-600 text-white"><Spinner /></span>,
    waiting: <span className="block size-6 rounded-full ring-2 ring-inset ring-slate-300 dark:ring-slate-600" />,
    failed: <span className="flex size-6 items-center justify-center rounded-full bg-red-600 text-xs text-white">!</span>,
    skipped: <span className="flex size-6 items-center justify-center rounded-full bg-slate-200 text-xs text-slate-500 dark:bg-slate-700 dark:text-slate-300">–</span>,
  };
  return (
    <ol className="space-y-3" data-testid="steps">
      {items.map((item) => (
        <li key={item.key} className="flex gap-3" data-step={item.key} data-state={item.state}>
          <span className="mt-0.5 shrink-0">{mark[item.state]}</span>
          <div className="min-w-0">
            <p className={cx("text-sm font-medium", item.state === "waiting" && "text-slate-500 dark:text-slate-400")}>{item.label}</p>
            {item.detail && <div className="text-sm text-slate-600 dark:text-slate-400">{item.detail}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Details({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return (
    <details className="group rounded-xl bg-white p-4 ring-1 ring-slate-200 sm:p-5 dark:bg-slate-900 dark:ring-slate-800">
      <summary className="cursor-pointer list-none text-sm font-medium text-slate-700 dark:text-slate-300">
        <span className="mr-2 inline-block transition group-open:rotate-90">›</span>{summary}
      </summary>
      <div className="mt-3">{children}</div>
    </details>
  );
}

export function Field({ label, hint, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">{label}</span>
      <input {...props} className="block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-base focus:border-slate-500 focus:outline-none sm:text-sm dark:border-slate-700 dark:bg-slate-950" />
      {hint && <span className="mt-1 block text-xs text-slate-500 dark:text-slate-400">{hint}</span>}
    </label>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const text = error instanceof Error ? error.message : "Something went wrong";
  return <p role="alert" className={cx("rounded-lg p-3 text-sm ring-1", tones.bad)}>{text}</p>;
}

export function Muted({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx("text-sm text-slate-500 dark:text-slate-400", className)}>{children}</p>;
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

/** "2 min ago", "3 h ago", or the date. */
export function formatWhen(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = (Date.now() - date.getTime()) / 1000;
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return date.toLocaleDateString();
}

export const TERMINAL = new Set(["READY", "REVIEW_REQUIRED", "BLOCKED", "FAILED", "CANCELLED"]);
