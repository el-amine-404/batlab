import { useState } from "react"
import { Sparkles } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api } from "@/lib/api"

/** Sign in; on the very first visit, create the account instead. */
export default function SignIn({ setup, onDone }: { setup: boolean; onDone: () => void }) {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError("")
    try {
      await api(setup ? "/api/setup" : "/api/login", { method: "POST", body: { username, password } })
      onDone()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="grid min-h-svh place-items-center bg-[radial-gradient(ellipse_at_top,_var(--color-sky-950)_0%,_transparent_60%)] p-6">
      <Card className="w-full max-w-sm">
        <CardHeader className="items-center text-center">
          <div className="mb-2 grid size-11 place-items-center rounded-xl bg-gradient-to-br from-sky-400 to-violet-500 text-white">
            <Sparkles className="size-5" />
          </div>
          <CardTitle className="text-xl">{setup ? "Welcome to Stanzarr" : "Sign in to Stanzarr"}</CardTitle>
          <CardDescription>{setup ? "Create the account that will manage your lyrics." : "Lyrics for your music library."}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="username">Username</Label>
              <Input id="username" autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} required />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input id="password" type="password" autoComplete={setup ? "new-password" : "current-password"}
                value={password} onChange={(e) => setPassword(e.target.value)} required minLength={setup ? 8 : 1} />
              {setup && <p className="text-xs text-muted-foreground">8 characters or more.</p>}
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy}>{setup ? "Create account" : "Sign in"}</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
