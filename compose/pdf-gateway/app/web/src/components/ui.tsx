import { cva, type VariantProps } from "class-variance-authority";
import { clsx, type ClassValue } from "clsx";
import {
  AlertCircle, Check, ChevronRight, Copy, Eye, EyeOff, Info, Loader2, Minus, ShieldAlert, ShieldCheck, ShieldX, X,
} from "lucide-react";
import { Slot } from "radix-ui";
import { useId, useState, type ComponentProps, type InputHTMLAttributes, type KeyboardEvent, type ReactNode, type SelectHTMLAttributes } from "react";
import { toast } from "sonner";
import { twMerge } from "tailwind-merge";

// Small primitives in the shadcn/ui style, on the theme's tokens (styles.css).
// Text props are rendered as React text nodes only; nothing here accepts HTML.

export function cx(...classes: ClassValue[]) {
  return twMerge(clsx(classes));
}

export const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-[color,background-color,border-color,box-shadow,opacity] outline-none focus-visible:ring-[3px] focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
  {
    variants: {
      variant: {
        primary: "bg-gradient-to-r from-emerald-400 to-cyan-400 text-primary-foreground shadow-lg shadow-emerald-950/40 hover:from-emerald-300 hover:to-cyan-300",
        secondary: "border border-border bg-secondary text-secondary-foreground hover:bg-accent",
        outline: "border border-border bg-transparent hover:bg-accent",
        ghost: "text-muted-foreground hover:bg-accent hover:text-foreground",
        danger: "border border-destructive/40 bg-destructive/10 text-destructive hover:bg-destructive/20",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-4",
        sm: "h-8 rounded-md px-3 text-xs",
        lg: "h-12 rounded-xl px-6 text-base",
        icon: "size-9",
        "icon-sm": "size-8 rounded-md",
      },
    },
    defaultVariants: { variant: "primary", size: "default" },
  },
);

export function Button({ className, variant, size, asChild = false, ...props }:
  ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "button";
  return <Comp data-slot="button" className={cx(buttonVariants({ variant, size }), className)} {...props} />;
}

/** For links that look like buttons (<a> downloads, router links). */
export const primaryLink = buttonVariants({ variant: "primary" });
export const secondaryLink = buttonVariants({ variant: "secondary" });

export function Card({ title, description, actions, children, className, icon }: {
  title?: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; icon?: ReactNode;
}) {
  return (
    <section className={cx("rounded-xl border border-border bg-card p-5 shadow-xl shadow-black/20 backdrop-blur-xl sm:p-6", className)}>
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            {icon && <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">{icon}</span>}
            <div className="min-w-0">
              {title && <h2 className="text-base font-semibold tracking-tight">{title}</h2>}
              {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
            </div>
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

/** A screen's title, a sentence under it, and its actions on the right. */
export function PageHeader({ title, description, actions, testId }: { title: ReactNode; description?: ReactNode; actions?: ReactNode; testId?: string }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl" data-testid={testId}>{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

const tones = {
  good: "border-success/30 bg-success/10 text-success",
  warn: "border-warning/30 bg-warning/10 text-warning",
  bad: "border-destructive/30 bg-destructive/10 text-destructive",
  info: "border-info/30 bg-info/10 text-info",
  muted: "border-border bg-muted text-muted-foreground",
};
export type Tone = keyof typeof tones;

export function Badge({ tone = "muted", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return <span className={cx("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium [&_svg]:size-3", tones[tone], className)}>{children}</span>;
}

const NOTICE_ICON: Record<Tone, ReactNode> = {
  good: <ShieldCheck />, warn: <ShieldAlert />, bad: <ShieldX />, info: <Loader2 className="animate-spin" />, muted: <Info />,
};

/** A coloured verdict: what happened, in words. */
export function Notice({ tone, title, children }: { tone: Tone; title: ReactNode; children?: ReactNode }) {
  return (
    <div className={cx("flex gap-4 rounded-xl border p-4 sm:p-5", tones[tone])} data-testid="verdict">
      <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-current/10 [&_svg]:size-5">{NOTICE_ICON[tone]}</span>
      <div className="min-w-0">
        <p className="text-base font-semibold">{title}</p>
        {children && <div className="mt-0.5 text-sm text-foreground/80">{children}</div>}
      </div>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 aria-hidden className={cx("size-4 animate-spin", className)} />;
}

export type StepState = "done" | "running" | "waiting" | "failed" | "skipped";

const MARK: Record<StepState, ReactNode> = {
  done: <span className="flex size-7 items-center justify-center rounded-full bg-success/15 text-success ring-1 ring-success/40"><Check className="size-4" /></span>,
  running: <span className="flex size-7 items-center justify-center rounded-full bg-info/15 text-info ring-1 ring-info/40"><Loader2 className="size-4 animate-spin" /></span>,
  waiting: <span className="block size-7 rounded-full border-2 border-dashed border-muted-foreground/40" />,
  failed: <span className="flex size-7 items-center justify-center rounded-full bg-destructive/15 text-destructive ring-1 ring-destructive/40"><X className="size-4" /></span>,
  skipped: <span className="flex size-7 items-center justify-center rounded-full bg-muted text-muted-foreground"><Minus className="size-4" /></span>,
};

/** A vertical timeline of steps, each with a state mark and a plain sentence. */
export function Steps({ items }: { items: { key: string; label: ReactNode; detail?: ReactNode; state: StepState }[] }) {
  return (
    <ol className="relative" data-testid="steps">
      {items.map((item, index) => (
        <li key={item.key} className="relative flex gap-4 pb-5 last:pb-0" data-step={item.key} data-state={item.state}>
          {index < items.length - 1 && <span aria-hidden className={cx("absolute top-8 bottom-1 left-3.5 w-px", item.state === "done" ? "bg-success/40" : "bg-border")} />}
          <span className="relative shrink-0">{MARK[item.state]}</span>
          <div className="min-w-0 pt-0.5">
            <p className={cx("text-sm font-medium", (item.state === "waiting" || item.state === "skipped") && "text-muted-foreground")}>{item.label}</p>
            {item.detail && <div className="mt-0.5 text-sm text-muted-foreground">{item.detail}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

export function Details({ summary, children, icon }: { summary: ReactNode; children: ReactNode; icon?: ReactNode }) {
  return (
    <details className="group rounded-xl border border-border bg-card backdrop-blur-xl">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-4 text-sm font-medium text-muted-foreground hover:text-foreground sm:px-6 [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 transition group-open:rotate-90" />{icon}{summary}
      </summary>
      <div className="border-t border-border px-5 py-4 sm:px-6">{children}</div>
    </details>
  );
}

export const inputClass = "block h-10 w-full min-w-0 rounded-lg border border-input bg-background/60 px-3 text-base text-foreground shadow-xs transition-[color,box-shadow,border-color] outline-none placeholder:text-muted-foreground/70 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40 disabled:opacity-50 aria-invalid:border-destructive sm:text-sm";

export function Field({ label, hint, className, ...props }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: ReactNode }) {
  const id = useId();
  return (
    <div className="block text-sm">
      <label htmlFor={props.id ?? id} className="mb-1.5 block font-medium">{label}</label>
      <input id={id} {...props} className={cx(inputClass, className)} />
      {hint && <span className="mt-1.5 block text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}

export function Select({ className, children, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cx(inputClass, "cursor-pointer pr-8", className)}>{children}</select>;
}

export function SelectField({ label, hint, children, ...props }: SelectHTMLAttributes<HTMLSelectElement> & { label: string; hint?: ReactNode }) {
  const id = useId();
  return (
    <div className="block text-sm">
      <label htmlFor={props.id ?? id} className="mb-1.5 block font-medium">{label}</label>
      <Select id={id} {...props}>{children}</Select>
      {hint && <span className="mt-1.5 block text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}

/** A password box with an eye to show what is typed, and a word when Caps Lock is on. */
export function PasswordField({ label, hint, ...props }: Omit<InputHTMLAttributes<HTMLInputElement>, "type"> & { label: string; hint?: ReactNode }) {
  const [shown, setShown] = useState(false);
  const [caps, setCaps] = useState(false);
  const id = useId();
  const watch = (event: KeyboardEvent<HTMLInputElement>) => setCaps(event.getModifierState("CapsLock"));
  return (
    <div className="block text-sm">
      <label htmlFor={props.id ?? id} className="mb-1.5 block font-medium">{label}</label>
      <div className="relative">
        <input id={id} {...props} type={shown ? "text" : "password"} onKeyUp={watch} onKeyDown={watch} onBlur={() => setCaps(false)}
          className={cx(inputClass, "pr-11")} />
        <button type="button" onClick={() => setShown(!shown)} aria-pressed={shown} aria-label={shown ? "Hide the password" : "Show the password"}
          title={shown ? "Hide it" : "Show it"}
          className="absolute top-1/2 right-1.5 flex size-8 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
          {shown ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </div>
      {caps && <span className="mt-1.5 flex items-center gap-1.5 text-xs text-warning" role="status"><AlertCircle className="size-3.5" /> Caps Lock is on.</span>}
      {hint && <span className="mt-1.5 block text-xs text-muted-foreground">{hint}</span>}
    </div>
  );
}

/** Two or three choices side by side, one picked. */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: ReactNode; testId?: string }[]; onChange: (value: T) => void; label: string;
}) {
  return (
    <div className="inline-flex rounded-lg border border-border bg-background/40 p-0.5" role="group" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" aria-pressed={value === option.value} data-testid={option.testId} onClick={() => onChange(option.value)}
          className={cx("flex h-8 items-center gap-1.5 rounded-md px-3 text-sm transition [&_svg]:size-4",
            value === option.value ? "bg-accent font-medium text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** The clipboard API needs a secure context; elsewhere a selected text area still copies. */
export async function toClipboard(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.className = "fixed opacity-0";
  document.body.append(area);
  area.select();
  const done = document.execCommand("copy");
  area.remove();
  if (!done) throw new Error("copy refused");
}

/** Copies text; a tick for a moment says it did. */
export function CopyButton({ text, label, className }: { text: string; label: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await toClipboard(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error(`Couldn't copy: select the ${label} and copy it by hand.`);
    }
  };
  return (
    <Button type="button" variant="ghost" size="icon-sm" className={className} onClick={() => void copy()} aria-label={`Copy the ${label}`} title={`Copy the ${label}`}>
      {copied ? <Check className="text-success" /> : <Copy />}
    </Button>
  );
}

/** A command to run, whole and wrapped (no scrollbar), with a copy button. */
export function CodeBlock({ code, label = "command", className }: { code: string; label?: string; className?: string }) {
  return (
    <div className={cx("flex items-start gap-1 rounded-lg border border-border bg-background/70 py-1 pr-1 pl-3", className)} data-testid="code-block">
      <code className="min-w-0 flex-1 py-1 font-mono text-xs leading-relaxed break-all whitespace-pre-wrap text-foreground">{code}</code>
      <CopyButton text={code} label={label} className="shrink-0" />
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const text = error instanceof Error ? error.message : "Something went wrong";
  return (
    <p role="alert" className={cx("flex items-start gap-2 rounded-lg border p-3 text-sm", tones.bad)}>
      <AlertCircle className="mt-0.5 size-4 shrink-0" /><span>{text}</span>
    </p>
  );
}

export function Muted({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx("text-sm text-muted-foreground", className)}>{children}</p>;
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
