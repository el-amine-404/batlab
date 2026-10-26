import { useState, type FormEvent, type ReactNode } from "react";
import { FolderTree, KeyRound, LogIn, ScanSearch, ShieldCheck, Sparkles, type LucideIcon } from "lucide-react";
import { api, type Session } from "../api";
import { Logo } from "../App";
import { Button, CodeBlock, ErrorNote, Field, PasswordField, Spinner, cx } from "../components/ui";

const STEPS: { icon: LucideIcon; title: string; text: string }[] = [
  { icon: ScanSearch, title: "Checked", text: "Viruses, scripts and hidden content, in a sealed sandbox." },
  { icon: ShieldCheck, title: "Rebuilt clean", text: "Every page redrawn, with searchable text. Your original never leaves quarantine." },
  { icon: FolderTree, title: "Filed for you", text: "By your rules, or by the AI on your own laptop. Then into Paperless." },
];

/** A page being checked: a beam passes over its lines. Drawn, no picture to load. */
function ScannedPage() {
  return (
    <div className="relative mx-auto h-56 w-44 overflow-hidden rounded-xl border border-white/10 bg-gradient-to-b from-white/[0.07] to-white/[0.02] p-5 shadow-2xl shadow-emerald-950/50" aria-hidden>
      <div className="mb-4 h-3 w-20 rounded-full bg-white/25" />
      {[90, 75, 85, 60, 80, 70, 45].map((width, index) => (
        <div key={index} className="mb-2.5 h-1.5 rounded-full bg-white/12" style={{ width: `${width}%` }} />
      ))}
      <div className="absolute right-4 bottom-4 flex size-9 items-center justify-center rounded-full bg-emerald-400/20 text-emerald-300 ring-1 ring-emerald-300/40">
        <ShieldCheck className="size-5" />
      </div>
      <div className="scan-beam absolute inset-x-0 top-0 h-6 bg-gradient-to-b from-transparent via-cyan-300/40 to-transparent" />
    </div>
  );
}

/** Before signing in: the gateway's idea on the left (large screens), the form on the right. */
function Frame({ title, text, children }: { title: string; text: ReactNode; children: ReactNode }) {
  return (
    <div className="relative min-h-dvh lg:grid lg:grid-cols-[minmax(0,1.1fr)_minmax(26rem,1fr)]">
      <div className="backdrop" aria-hidden />
      <section className="relative hidden flex-col justify-center px-14 lg:flex xl:px-20">
        <div className="max-w-lg">
          <div className="mb-10 flex items-center gap-3 text-lg font-semibold tracking-tight"><Logo className="size-10" /> PDF Gateway</div>
          <h2 className="text-4xl leading-tight font-semibold tracking-tight xl:text-5xl">
            Every PDF checked{" "}
            <span className="bg-gradient-to-r from-emerald-300 to-cyan-300 bg-clip-text text-transparent">before it's kept.</span>
          </h2>
          <div className="mt-10 flex items-center gap-10">
            <ScannedPage />
            <ol className="flex flex-col gap-5">
              {STEPS.map(({ icon: Icon, title: step, text: about }) => (
                <li key={step} className="flex gap-3.5">
                  <span className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-emerald-300/20 bg-emerald-400/10 text-emerald-300 backdrop-blur">
                    <Icon className="size-5" />
                  </span>
                  <span>
                    <span className="block font-medium">{step}</span>
                    <span className="block text-sm text-muted-foreground">{about}</span>
                  </span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>
      <section className="flex min-h-dvh items-center justify-center px-4 py-10 lg:min-h-0">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex flex-col items-center gap-3 text-center">
            <Logo className="size-12 lg:hidden" />
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            <p className="text-sm text-muted-foreground">{text}</p>
          </div>
          <div className="rounded-xl border border-white/10 bg-card/70 p-5 shadow-2xl shadow-black/40 backdrop-blur-xl">{children}</div>
        </div>
      </section>
    </div>
  );
}

function SignIn({ onLogin }: { onLogin: (session: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [shake, setShake] = useState(0);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await api.login(username.trim(), password));
    } catch (caught) {
      setError(caught);
      setPassword("");
      setShake((n) => n + 1);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Frame title="Sign in to PDF Gateway" text="Ask an admin for an account if you don't have one.">
      <form key={shake} onSubmit={submit} className={cx("flex flex-col gap-4", shake > 0 && "animate-[shake_0.35s]")}>
        <Field label="User name" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required autoFocus
          value={username} onChange={(event) => setUsername(event.target.value)} />
        <PasswordField label="Password" name="password" autoComplete="current-password" required
          value={password} onChange={(event) => setPassword(event.target.value)} />
        <ErrorNote error={error} />
        <Button type="submit" disabled={busy || !username.trim() || !password} className="w-full">
          {busy ? <><Spinner /> Signing in…</> : <><LogIn /> Sign in</>}
        </Button>
      </form>
    </Frame>
  );
}

/** "57lj y5bs-l5nr" → "57LJ-Y5BS-L5NR": its 12 letters and digits, the dashes put in as it is typed (or pasted). */
function formatCode(typed: string) {
  const raw = typed.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 12);
  return raw.match(/.{1,4}/g)?.join("-") ?? "";
}

const SETUP_COMMAND = "docker logs pdf-gateway-api 2>&1 | grep 'setup code'";

function Setup({ suggested, onLogin }: { suggested: string | null; onLogin: (session: Session) => void }) {
  const [code, setCode] = useState("");
  const [username, setUsername] = useState(suggested ?? "");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const mismatch = again.length > 0 && again !== password;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError(null);
    try {
      onLogin(await api.setup(code, username.trim(), password));
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Frame title="Set up PDF Gateway" text="Make the admin account: the admin then adds the others and decides what each may do.">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div>
          <div className="relative">
            <Field label="Setup code" name="setup-code" className="pl-9 font-mono tracking-widest" placeholder="ABCD-EFGH-JKLM" autoComplete="off"
              autoCapitalize="characters" spellCheck={false} required autoFocus maxLength={14}
              value={code} onChange={(event) => setCode(formatCode(event.target.value))} />
            <KeyRound className="pointer-events-none absolute bottom-3 left-3 size-4 text-muted-foreground" />
          </div>
          <p className="mt-1.5 mb-1.5 text-xs text-muted-foreground">12 letters and digits, in the API's log. On the server:</p>
          <CodeBlock code={SETUP_COMMAND} />
        </div>
        <Field label="User name" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required
          hint={suggested ? `"${suggested}" keeps the documents added with the old access token.` : "Letters, digits, . _ and -."}
          value={username} onChange={(event) => setUsername(event.target.value)} />
        <PasswordField label="Password" name="new-password" autoComplete="new-password" required minLength={10} hint="At least 10 characters."
          value={password} onChange={(event) => setPassword(event.target.value)} />
        <PasswordField label="Password again" name="new-password-again" autoComplete="new-password" required aria-invalid={mismatch}
          value={again} onChange={(event) => setAgain(event.target.value)} />
        {mismatch && <p className="text-sm text-destructive">The two passwords differ.</p>}
        <ErrorNote error={error} />
        <Button type="submit" className="w-full"
          disabled={busy || code.length < 14 || !username.trim() || password.length < 10 || !again || mismatch}>
          {busy ? <><Spinner /> Making it…</> : <><Sparkles /> Make the admin account</>}
        </Button>
      </form>
    </Frame>
  );
}

/** Signing in; or, until an admin can sign in, making one with the setup code. */
export function LoginPage({ setup, suggested, onLogin }: { setup: boolean; suggested: string | null; onLogin: (session: Session) => void }) {
  return setup ? <Setup suggested={suggested} onLogin={onLogin} /> : <SignIn onLogin={onLogin} />;
}
