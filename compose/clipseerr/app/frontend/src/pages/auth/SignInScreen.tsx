import { Download, PlayCircle, Sparkles, Eye, EyeOff, KeyRound, Loader2, LogIn, type LucideIcon, ShieldCheck,
         TriangleAlert } from "lucide-react";
import { type ComponentProps, type FormEvent, type KeyboardEvent, type ReactNode, useState } from "react";

import { Drift } from "@/components/auth/Drift";
import { Logo } from "@/components/Logo";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorText } from "@/lib/api";
import { restart } from "@/lib/auth";
import { cn } from "@/lib/utils";

function Field({ id, label, hint, children }: { id: string; label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

/** A password box with an eye to show what is typed, and a word when Caps Lock is on. */
function PasswordInput({ className, ...props }: Omit<ComponentProps<typeof Input>, "type">) {
  const [shown, setShown] = useState(false);
  const [caps, setCaps] = useState(false);
  const watch = (event: KeyboardEvent<HTMLInputElement>) => setCaps(event.getModifierState("CapsLock"));
  return (
    <>
      <div className="relative">
        <Input {...props} type={shown ? "text" : "password"} className={cn("pr-11", className)} onKeyUp={watch} onKeyDown={watch}
               onBlur={() => setCaps(false)} />
        <button type="button" onClick={() => setShown(!shown)} aria-label={shown ? "Hide the password" : "Show the password"}
                aria-pressed={shown} title={shown ? "Hide it" : "Show it"}
                className="absolute top-1/2 right-1.5 flex size-8 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
          {shown ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </div>
      {caps && (
        <p className="flex items-center gap-1.5 text-xs text-warning" role="status">
          <TriangleAlert className="size-3.5" /> Caps Lock is on.
        </p>
      )}
    </>
  );
}

const STEPS: { icon: LucideIcon; title: string; text: string }[] = [
  { icon: Sparkles, title: "The newest music videos", text: "From YouTube Music and Apple Music, and what Reddit loves." },
  { icon: PlayCircle, title: "Watch them here", text: "Before anyone downloads anything." },
  { icon: Download, title: "Ask for the song or the video", text: "Into Navidrome and Jellyfin, without leaving the page." },
];

/**
 * The screen before signing in: a sky that drifts (the aurora) behind it all;
 * on a large screen, clipseerr's idea on the left, its things drifting into a
 * soft glow (components/auth/Drift.tsx), and the form on the right. Nothing in
 * it comes from clipseerr's own: whoever isn't signed in sees none of it.
 */
function Frame({ title, text, shake, children }: { title: string; text: ReactNode; shake?: number; children: ReactNode }) {
  return (
    <div className="relative min-h-dvh lg:grid lg:grid-cols-[minmax(0,1.1fr)_minmax(26rem,1fr)]">
      <div className="aurora fixed! -z-10" aria-hidden><span /><span /><span /></div>

      <section className="relative hidden overflow-hidden lg:flex lg:flex-col lg:justify-center lg:px-14 xl:px-20">
        {/* The frames fade out behind the words, to stay a background. */}
        <Drift kind="clip" colour="253,164,175" light="254,215,170" className="absolute inset-0 size-full [mask-image:radial-gradient(ellipse_55%_45%_at_32%_50%,transparent_35%,black_80%)]" />
        <div className="relative max-w-lg">
          <div className="mb-10 flex items-center gap-3 text-lg font-semibold tracking-tight">
            <Logo className="size-10" /> clipseerr
          </div>
          <h2 className="text-4xl leading-tight font-semibold tracking-tight xl:text-5xl">
            Watch it.{" "}
            <span className="bg-gradient-to-r from-rose-300 to-orange-300 bg-clip-text text-transparent">Ask for it.</span>
          </h2>
          <ol className="mt-10 flex flex-col gap-5">
            {STEPS.map(({ icon: Icon, title: step, text: about }, n) => (
              <li key={step} className="flex gap-4">
                <span className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-rose-300/20 bg-rose-400/10 text-rose-300 backdrop-blur">
                  <Icon className="size-5" />
                </span>
                <span>
                  <span className="block font-medium"><span className="mr-1.5 text-muted-foreground tabular-nums">{n + 1}.</span>{step}</span>
                  <span className="block text-sm text-muted-foreground">{about}</span>
                </span>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="flex min-h-dvh items-center justify-center px-4 py-10 lg:min-h-0">
        <div className="w-full max-w-sm">
          <div className="mb-6 flex flex-col items-center gap-3 text-center">
            <Logo className="size-12 lg:hidden" />
            <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
            <p className="text-sm text-muted-foreground">{text}</p>
          </div>
          {/* key: a new value plays the shake again. */}
          <div key={shake} className={cn("rounded-xl border border-white/10 bg-card/70 p-5 shadow-2xl shadow-black/40 backdrop-blur-xl", shake && "shake")}>
            {children}
          </div>
        </div>
      </section>
    </div>
  );
}

function SignIn() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [shake, setShake] = useState(0);

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
      setShake((n) => n + 1);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Frame title="Sign in to clipseerr" text="Ask an admin for an account if you don't have one." shake={shake}>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field id="login-user" label="User name">
          <Input id="login-user" autoComplete="username" autoCapitalize="none" spellCheck={false} required autoFocus
                 value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <Field id="login-password" label="Password">
          <PasswordInput id="login-password" autoComplete="current-password" required
                         value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
        <Button type="submit" disabled={busy || !username || !password}>
          {busy ? <><Loader2 className="animate-spin" /> Signing in…</> : <><LogIn /> Sign in</>}
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
  const [shake, setShake] = useState(0);
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
      setShake((n) => n + 1);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Frame title="Set up clipseerr" text="Make the first account: the admin, who then adds the others and decides what each may do." shake={shake}>
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field id="setup-code" label="Setup code"
               hint={<>12 letters and digits; the dashes are put in for you. It is in clipseerr's log, or given by{" "}
                       <code className="text-foreground">docker exec clipseerr admin setup-code</code> on the server.</>}>
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
        <Field id="setup-name" label="Your name" hint="How the page and the notifications name you.">
          <Input id="setup-name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id="setup-password" label="Password" hint="At least 10 characters.">
          <PasswordInput id="setup-password" autoComplete="new-password" required minLength={10}
                         value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field id="setup-again" label="Password again">
          <PasswordInput id="setup-again" autoComplete="new-password" required aria-invalid={mismatch}
                         value={again} onChange={(e) => setAgain(e.target.value)} />
        </Field>
        {mismatch && <p className="text-sm text-destructive">The two passwords differ.</p>}
        {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
        <Button type="submit" disabled={busy || code.length < 14 || !username || password.length < 10 || mismatch || !again}>
          {busy ? <><Loader2 className="animate-spin" /> Making it…</> : <><ShieldCheck /> Make the admin account</>}
        </Button>
      </form>
    </Frame>
  );
}

/** Signing in; or, before anyone exists, making the first admin with the setup code. */
export function SignInScreen({ setup }: { setup: boolean }) {
  return setup ? <Setup /> : <SignIn />;
}
