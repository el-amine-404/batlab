import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Check, Eye } from "lucide-react";
import { toast } from "sonner";
import { api } from "../api";
import { useSession } from "../App";
import { Badge, Card, ErrorNote, Muted, PageHeader, Spinner, cx } from "../components/ui";
import { AccountsCard, PasswordCard } from "./Accounts";

/** Your account; for admins, the AI model and the people and apps that may use the gateway. */
export function SettingsPage() {
  const session = useSession();
  const admin = session.role === "ADMIN";
  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description={admin ? "Your password, the AI model, and who may use the gateway." : "Your password."} />
      <PasswordCard />
      {admin && <ModelCard />}
      {admin && <AccountsCard me={session.username} />}
    </div>
  );
}

function size(bytes: number | null | undefined) {
  return bytes ? `${(bytes / 1024 ** 3).toFixed(bytes > 10 * 1024 ** 3 ? 0 : 1)} GB` : "";
}

/** Which model on the laptop files documents: the ones LM Studio has installed, as the agent reports them. */
function ModelCard() {
  const client = useQueryClient();
  const ai = useQuery({ queryKey: ["ai"], queryFn: api.ai, refetchInterval: 20_000 });
  const choose = useMutation({
    mutationFn: (model: string | null) => api.chooseModel(model),
    onSuccess: (value, model) => {
      client.setQueryData(["ai"], value);
      toast.success(`${model ?? "The laptop's own default"} files documents from now on.`,
        { description: "The laptop loads it for the next document (the first one takes a little longer)." });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "That didn't work."),
  });
  const a = ai.data;
  const models = a?.models ?? [];
  const current = a?.chosenModel ?? null;
  const option = "flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-sm transition disabled:opacity-60";
  return (
    <Card title="AI model" icon={<Bot className="size-5" />}
      description="The model on your laptop that reads each document and files it. Bigger is usually better and slower; every run records which model did it.">
      <ErrorNote error={ai.error} />
      {a && !models.length && <Muted>The laptop hasn't reported its models yet: start the AI (System shows how), and they appear here.</Muted>}
      {models.length > 0 && (
        <ul className="grid gap-2 sm:grid-cols-2" data-testid="models">
          <li>
            <button type="button" disabled={choose.isPending} onClick={() => choose.mutate(null)}
              className={cx(option, current === null ? "border-primary/60 bg-primary/10" : "border-border hover:bg-accent")}>
              <span className="min-w-0 flex-1"><span className="block font-medium">The laptop's default</span>
                <span className="block truncate text-xs text-muted-foreground">Set in its agent.env{a?.activeModel && !current ? `: ${a.activeModel}` : ""}</span></span>
              {current === null && <Check className="size-4 text-primary" />}
            </button>
          </li>
          {models.map((model) => (
            <li key={model.key}>
              <button type="button" disabled={choose.isPending} onClick={() => choose.mutate(model.key)} data-model={model.key}
                className={cx(option, current === model.key ? "border-primary/60 bg-primary/10" : "border-border hover:bg-accent")}>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{model.key}</span>
                  <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                    {size(model.bytes)}
                    {model.loaded && <Badge tone="good">loaded</Badge>}
                    {model.vision && <Badge tone="info"><Eye /> vision</Badge>}
                  </span>
                </span>
                {choose.isPending && choose.variables === model.key ? <Spinner /> : current === model.key && <Check className="size-4 text-primary" />}
              </button>
            </li>
          ))}
        </ul>
      )}
      <Muted className="mt-3 text-xs">
        The gateway gives every model the document's text only (vision models too) and checks each answer against that text.
        The laptop unloads the previous model before loading another, so two big ones never share its memory.
      </Muted>
    </Card>
  );
}
