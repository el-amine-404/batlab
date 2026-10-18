import { LogView } from "@/components/common/LogView";
import { PageHeader } from "@/components/layout/States";
import { api, type ServerLogLine } from "@/lib/api";
import { useLogLines } from "@/lib/useLogLines";

/** videodrop's own log, its newest lines, followed live. */
export default function LogPage() {
  const { lines, loading, error } = useLogLines<ServerLogLine>("server", (after, signal) => api.serverLog(after, signal), true, 5000);
  return (
    <>
      <PageHeader eyebrow="Settings" title="Log"
                  description="What videodrop itself says: changes asked through the API, retries, errors with their traceback, housekeeping. Each request's id (X-Request-ID) is in its line. The newest 2000 lines since the start; passwords, tokens and signed addresses are hidden. Each download has its own log on its page." />
      <LogView lines={lines} loading={loading} error={error} height="h-[min(70vh,48rem)]" />
    </>
  );
}
