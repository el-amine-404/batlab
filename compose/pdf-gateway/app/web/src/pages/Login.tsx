import { useState, type FormEvent } from "react";
import { api, type Session } from "../api";
import { Button, Card, ErrorNote, Field } from "../components/ui";

/** The API token is used once to open a session; it is never stored by the page. */
export function LoginPage({ onLogin }: { onLogin: (session: Session) => void }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const session = await api.login(token.trim());
      setToken("");
      onLogin(session);
    } catch {
      setError(new Error("That token was not accepted."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-4">
      <Card title="PDF Gateway">
        <form onSubmit={submit} className="space-y-4">
          <p className="text-sm text-slate-600">Sign in with your API token. It opens a browser session and is not kept on this device.</p>
          <Field label="API token" type="password" name="token" autoComplete="off" autoCapitalize="off" spellCheck={false}
            value={token} onChange={(event) => setToken(event.target.value)} required minLength={32} />
          <ErrorNote error={error} />
          <Button type="submit" disabled={busy || token.trim().length < 32} className="w-full">{busy ? "Signing in…" : "Sign in"}</Button>
        </form>
      </Card>
    </main>
  );
}
