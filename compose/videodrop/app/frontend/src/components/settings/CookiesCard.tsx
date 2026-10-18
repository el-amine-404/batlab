import { Cookie, Loader2, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { api, type CookieStatus, errorText } from "@/lib/api";

const day = (seconds: number) =>
  new Date(seconds * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

function describe(status: CookieStatus): string {
  if (!status.present || !status.saved_at) return "None. Age-restricted and sign-in-only videos can't be downloaded.";
  if (status.expired) return `Saved ${day(status.saved_at)}, and they have run out. Upload new ones.`;
  if (status.expires_at) return `Saved ${day(status.saved_at)}. They run out on ${day(status.expires_at)}.`;
  return `Saved ${day(status.saved_at)}.`;
}

/** The YouTube cookies every download uses, for videos YouTube shows only to a
 * signed-in viewer. The page can replace or remove them, never read them. */
export function CookiesCard() {
  const [status, setStatus] = useState<CookieStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.cookies().then(setStatus, () => setStatus(null));
  }, []);

  const run = async (action: () => Promise<CookieStatus | void>, done: string) => {
    setBusy(true);
    try {
      const next = await action();
      setStatus(next ?? { present: false });
      toast.success(done);
    } catch (caught) {
      toast.error("Couldn't change the cookies", { description: errorText(caught) });
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };

  return (
    <Card className="p-4">
      <div className="flex items-start gap-3">
        <Cookie className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">YouTube cookies</p>
          <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
            {status ? describe(status) : "…"}
          </p>
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground/80">
            Export youtube.com's cookies as a cookies.txt file from a private browser window signed in to YouTube
            (an extension such as "Get cookies.txt LOCALLY" does it), then close that window. A spare account is
            safer: YouTube may sign out cookies used for downloading.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <input
              ref={input}
              type="file"
              accept=".txt,text/plain"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void run(() => api.saveCookies(file), "Cookies saved");
              }}
            />
            <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => input.current?.click()}>
              {busy ? <Loader2 className="animate-spin" /> : <Upload />}
              {status?.present ? "Replace cookies.txt" : "Upload cookies.txt"}
            </Button>
            {status?.present && (
              <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => void run(api.removeCookies, "Cookies removed")}>
                <Trash2 /> Remove
              </Button>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
