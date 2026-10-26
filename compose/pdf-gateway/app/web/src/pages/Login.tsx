import { useState, type FormEvent, type ReactNode } from "react";
import { api, type Session } from "../api";
import { Button, ErrorNote, Field, Muted, PasswordField } from "../components/ui";

/** The logo, a title and a sentence above the form, centred. */
function Frame({ title, text, children }: { title: string; text: ReactNode; children: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-4 py-10">
      <div className="mb-6 flex flex-col items-center gap-3 text-center">
        <img src="/icon.svg" alt="" width={48} height={48} className="size-12 rounded-xl" />
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <Muted>{text}</Muted>
      </div>
      <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-slate-200 dark:bg-slate-900 dark:shadow-none dark:ring-slate-800">
        {children}
      </div>
    </main>
  );
}

function SignIn({ onLogin }: { onLogin: (session: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await api.login(username.trim(), password));
    } catch (caught) {
      setError(caught);
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Frame title="Sign in to PDF Gateway" text="Ask an admin for an account if you don't have one.">
      <form onSubmit={submit} className="space-y-4">
        <Field label="User name" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required autoFocus
          value={username} onChange={(event) => setUsername(event.target.value)} />
        <PasswordField label="Password" name="password" autoComplete="current-password" required
          value={password} onChange={(event) => setPassword(event.target.value)} />
        <ErrorNote error={error} />
        <Button type="submit" disabled={busy || !username.trim() || !password} className="w-full">{busy ? "Signing in…" : "Sign in"}</Button>
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
      <form onSubmit={submit} className="space-y-4">
        <div>
          <Field label="Setup code" name="setup-code" placeholder="ABCD-EFGH-JKLM" autoComplete="off"
            autoCapitalize="characters" spellCheck={false} required autoFocus maxLength={14}
            value={code} onChange={(event) => setCode(formatCode(event.target.value))} />
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">In the API's log on the server:</p>
          <code className="mt-1 block overflow-x-auto whitespace-nowrap rounded-md bg-slate-100 px-2 py-1 text-xs dark:bg-slate-800">{SETUP_COMMAND}</code>
        </div>
        <Field label="User name" name="username" autoComplete="username" autoCapitalize="none" spellCheck={false} required
          hint={suggested ? `"${suggested}" keeps the documents added with the old access token.` : "Letters, digits, . _ and -."}
          value={username} onChange={(event) => setUsername(event.target.value)} />
        <PasswordField label="Password" name="new-password" autoComplete="new-password" required minLength={10} hint="At least 10 characters."
          value={password} onChange={(event) => setPassword(event.target.value)} />
        <PasswordField label="Password again" name="new-password-again" autoComplete="new-password" required aria-invalid={mismatch}
          value={again} onChange={(event) => setAgain(event.target.value)} />
        {mismatch && <p className="text-sm text-red-700 dark:text-red-400">The two passwords differ.</p>}
        <ErrorNote error={error} />
        <Button type="submit" className="w-full"
          disabled={busy || code.length < 14 || !username.trim() || password.length < 10 || !again || mismatch}>
          {busy ? "Making it…" : "Make the admin account"}
        </Button>
      </form>
    </Frame>
  );
}

/** Signing in; or, until an admin can sign in, making one with the setup code. */
export function LoginPage({ setup, suggested, onLogin }: { setup: boolean; suggested: string | null; onLogin: (session: Session) => void }) {
  return setup ? <Setup suggested={suggested} onLogin={onLogin} /> : <SignIn onLogin={onLogin} />;
}
