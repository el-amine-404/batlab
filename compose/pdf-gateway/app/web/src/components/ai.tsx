import type { ReactNode } from "react";
import type { z } from "zod";
import type { Ai as AiSchema } from "../api";
import { CodeBlock } from "./ui";

type Ai = z.infer<typeof AiSchema>;

/** The AI answers jobs: the agent is in touch and LM Studio has the model. */
export function aiUp(state: string | undefined) {
  return !!state && ["READY", "AVAILABLE", "BUSY"].includes(state);
}

const DEFAULT_MODEL = "qwen/qwen3-30b-a3b-2507";
const AGENT = "docker compose -f compose/pdf-gateway/app/enrichment-agent/compose.yml up -d --build";

function Step({ n, text, children }: { n: number; text: ReactNode; children?: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">{n}</span>
      <div className="min-w-0 flex-1 space-y-1.5"><p className="text-sm">{text}</p>{children}</div>
    </li>
  );
}

/** What to run to bring the AI back, for the state it is in; nothing when it works. */
export function AiSetup({ ai }: { ai: Ai | undefined }) {
  if (!ai || aiUp(ai.state)) return null;
  const model = ai.model ?? DEFAULT_MODEL;
  const lms = [
    <Step key="server" n={1} text="On the laptop, start LM Studio's server:"><CodeBlock code="lms server start" /></Step>,
    <Step key="load" n={2} text={<>Load the model (8k context, what the agent asks for):</>}>
      <CodeBlock code={`lms load ${model} --context-length 8192 -y`} />
    </Step>,
  ];
  let steps: ReactNode[];
  switch (ai.state) {
    case "DISABLED":
      steps = [
        <Step key="env" n={1} text={<>On the server, turn it on in <code>compose/.env</code>:</>}><CodeBlock code="PDF_GATEWAY_ENRICHMENT_ENABLED=true" label="setting" /></Step>,
        <Step key="api" n={2} text="Restart the API so it reads it:"><CodeBlock code="docker compose -f compose/pdf-gateway/docker-compose.yml up -d api" /></Step>,
        <Step key="laptop" n={3} text="Then set up the laptop (LM Studio and the agent) as the README's M7 section says." />,
      ];
      break;
    case "MODEL_MISSING":
      steps = [<Step key="get" n={1} text="On the laptop, download the model:"><CodeBlock code={`lms get ${model}`} /></Step>,
        <Step key="load" n={2} text="Load it:"><CodeBlock code={`lms load ${model} --context-length 8192 -y`} /></Step>];
      break;
    case "SERVICE_DOWN":
      steps = lms;
      break;
    case "DEGRADED":
      steps = [<Step key="logs" n={1} text="It paused after answers it couldn't use, and tries again by itself. To see why, on the laptop:">
        <CodeBlock code="docker logs --tail 50 pdf-gateway-enrichment-agent-agent-1" /></Step>];
      break;
    default: // UNKNOWN, OFFLINE: the agent isn't in touch (laptop off, asleep, or the agent stopped).
      steps = [...lms, <Step key="agent" n={3} text={<>Start the agent, from the batlab folder (it needs <code>agent.env</code>, from <code>agent.env.example</code>, the first time):</>}>
        <CodeBlock code={AGENT} /></Step>];
  }
  return (
    <div className="space-y-3 rounded-lg border border-border bg-background/40 p-4" data-testid="ai-setup">
      <p className="text-sm font-medium">How to start it</p>
      <ol className="space-y-3">{steps}</ol>
      <p className="text-xs text-muted-foreground">The gateway notices within a minute; files waiting for it are filed then.</p>
    </div>
  );
}
