import { KeyRound, Loader2, LogIn, ShieldCheck } from "lucide-react";
import { type FormEvent, type ReactNode, useState } from "react";

import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText } from "@/lib/api";
import { restart } from "@/lib/auth";

function Field({ id, label, hint, children }: { id: string; label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function Frame({ title, text, children }: { title: string; text: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="backdrop" aria-hidden />
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-3 text-center">
          <Logo className="size-12" />
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="text-sm text-muted-foreground">{text}</p>
        </div>
        <div className="rounded-xl border border-border bg-card/80 p-5 shadow-xl backdrop-blur">{children}</div>
      </div>
    </div>
  );
}

function SignIn() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      await api.login(username, password);
      restart();
    } catch (caught) {
      setProblem(errorText(caught, "Couldn't sign in."));
      setPassword("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Frame title="Sign in to videodrop" text="Ask an admin for an account if you don't have one.">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field id="login-user" label="User name">
          <Input id="login-user" autoComplete="username" autoCapitalize="none" spellCheck={false} required autoFocus
                 value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field id="login-password" label="Password">
          <Input id="login-password" type="password" autoComplete="current-password" required
                 value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
        <Button type="submit" disabled={busy || !username || !password}>
          {busy ? <Loader2 className="animate-spin" /> : <LogIn />} Sign in
        </Button>
      </form>
    </Frame>
  );
}

/** "57lj y5bs-l5nr" → "57LJ-Y5BS-L5NR": its 12 letters and digits, the dashes put in as it is typed (or pasted). */
function formatCode(typed: string): string {
  const raw = typed.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 12);
  return raw.match(/.{1,4}/g)?.join("-") ?? "";
}

function Setup() {
  const [code, setCode] = useState("");
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const mismatch = again.length > 0 && again !== password;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setProblem(null);
    try {
      await api.setup({ code, username, name, password });
      restart();
    } catch (caught) {
      setProblem(errorText(caught, "Couldn't set up."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Frame title="Set up videodrop" text="Make the first account: the admin, who then adds the others and decides what each may do.">
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field id="setup-code" label="Setup code"
               hint={<>12 letters and digits; the dashes are put in for you. It is in videodrop's log, or given by{" "}
                       <code className="text-foreground">docker exec videodrop python -m videodrop.admin setup-code</code> on the server.</>}>
          <div className="relative">
            <KeyRound className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input id="setup-code" className="pl-9 font-mono tracking-widest" placeholder="ABCD-EFGH-JKLM" autoComplete="off"
                   autoCapitalize="characters" spellCheck={false} required autoFocus inputMode="text" maxLength={14}
                   value={code} onChange={(e) => setCode(formatCode(e.target.value))} />
          </div>
        </Field>
        <Field id="setup-user" label="User name" hint="What you sign in with: letters, digits, . _ and -.">
          <Input id="setup-user" autoComplete="username" autoCapitalize="none" spellCheck={false} required
                 value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field id="setup-name" label="Your name" hint="How downloads and notifications name you.">
          <Input id="setup-name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id="setup-password" label="Password" hint="At least 10 characters.">
          <Input id="setup-password" type="password" autoComplete="new-password" required minLength={10}
                 value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field id="setup-again" label="Password again">
          <Input id="setup-again" type="password" autoComplete="new-password" required aria-invalid={mismatch}
                 value={again} onChange={(e) => setAgain(e.target.value)} />
        </Field>
        {mismatch && <p className="text-sm text-destructive">The two passwords differ.</p>}
        {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
        <Button type="submit" disabled={busy || code.length < 14 || !username || password.length < 10 || mismatch || !again}>
          {busy ? <Loader2 className="animate-spin" /> : <ShieldCheck />} Make the admin account
        </Button>
      </form>
    </Frame>
  );
}

/** Signing in; or, before anyone exists, making the first admin with the setup code. */
export function SignInScreen({ setup }: { setup: boolean }) {
  return setup ? <Setup /> : <SignIn />;
}
