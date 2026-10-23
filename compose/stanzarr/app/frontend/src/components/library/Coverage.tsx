import { ChartPie, ChartBarStacked } from "lucide-react";
import { useState } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Coverage as Counts, CoverageDay, LyricsStatus } from "@/lib/api";
import { STATUS, STATUS_ORDER } from "@/lib/format";
import { cn } from "@/lib/utils";

const SCROLLS: LyricsStatus[] = ["synced", "ai_corrected", "ai_checked", "ai_unchecked"];
const VIEW = "stanzarr:coverage-view";

function readView(): "bar" | "donut" {
  try {
    return localStorage.getItem(VIEW) === "donut" ? "donut" : "bar";
  } catch {
    return "bar";
  }
}

const share = (n: number, total: number) => (total ? `${((n / total) * 100).toFixed(n / total < 0.1 ? 1 : 0)}%` : "0%");

/** What a part says on hover: its name, how many, what share, what it means. */
function Tip({ status, count, total }: { status: LyricsStatus; count: number; total: number }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="flex items-center gap-1.5 font-medium">
        <span className="size-2 rounded-full" style={{ background: STATUS[status].color }} /> {STATUS[status].label}
      </span>
      <span className="tabular-nums">{count.toLocaleString()} songs · {share(count, total)}</span>
      <span className="text-muted-foreground">{STATUS[status].hint}</span>
      <span className="text-muted-foreground">Click to show them.</span>
    </div>
  );
}

function Donut({ counts, total, chosen, onChoose }: { counts: Counts; total: number; chosen: LyricsStatus[];
                                                      onChoose: (s: LyricsStatus) => void }) {
  const r = 52;
  const c = 2 * Math.PI * r;
  // A 2px gap between parts (of the circle's length), so neighbours stay apart.
  const gap = 2;
  let at = 0;
  const parts = STATUS_ORDER.filter((s) => counts[s]).map((s) => {
    const length = (counts[s] / total) * c;
    const part = { s, from: at, length };
    at += length;
    return part;
  });
  const scrolls = SCROLLS.reduce((sum, s) => sum + counts[s], 0);
  return (
    <svg viewBox="0 0 140 140" className="size-36 shrink-0" role="img"
         aria-label={STATUS_ORDER.map((s) => `${STATUS[s].label} ${counts[s]}`).join(", ")}>
      <g transform="rotate(-90 70 70)">
        {parts.map(({ s, from, length }) => (
          <Tooltip key={s}>
            <TooltipTrigger asChild>
              <circle cx="70" cy="70" r={r} fill="none" stroke={STATUS[s].color} strokeWidth="16"
                      strokeDasharray={`${Math.max(0.5, length - (parts.length > 1 ? gap : 0))} ${c}`} strokeDashoffset={-from}
                      className={cn("cursor-pointer transition-opacity", chosen.length && !chosen.includes(s) && "opacity-30")}
                      onClick={() => onChoose(s)} />
            </TooltipTrigger>
            <TooltipContent side="right"><Tip status={s} count={counts[s]} total={total} /></TooltipContent>
          </Tooltip>
        ))}
      </g>
      <text x="70" y="68" textAnchor="middle" className="fill-foreground text-[22px] font-semibold">{share(scrolls, total)}</text>
      <text x="70" y="86" textAnchor="middle" className="fill-muted-foreground text-[10px]">scroll</text>
    </svg>
  );
}

/** The share that scrolls, day by day: one line, with a crosshair and its numbers on hover. */
function History({ days }: { days: CoverageDay[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (days.length < 2) {
    return <p className="text-xs text-muted-foreground">The history starts today: a point a day, after each day's last scan.</p>;
  }
  const W = 600, H = 120, P = 6;
  const points = days.map((d) => {
    const total = STATUS_ORDER.reduce((sum, s) => sum + (d.counts[s] ?? 0), 0);
    const scrolls = SCROLLS.reduce((sum, s) => sum + (d.counts[s] ?? 0), 0);
    return { day: d.day, total, scrolls, value: total ? scrolls / total : 0 };
  });
  const min = Math.max(0, Math.floor(Math.min(...points.map((p) => p.value)) * 10) / 10 - 0.05);
  const x = (i: number) => P + (i / (points.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - min) / (1 - min)) * (H - 2 * P);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join("");
  const shown = hover === null ? points[points.length - 1] : points[hover];
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs text-muted-foreground">
        <span>Songs that scroll, the last {points.length} days</span>
        <span className="tabular-nums text-foreground">
          {new Date(`${shown.day}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" })}:{" "}
          {Math.round(shown.value * 100)}% · {shown.scrolls.toLocaleString()} of {shown.total.toLocaleString()}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-28 w-full" preserveAspectRatio="none" role="img"
           aria-label={`From ${Math.round(points[0].value * 100)}% to ${Math.round(points[points.length - 1].value * 100)}%`}
           onMouseLeave={() => setHover(null)}
           onMouseMove={(e) => {
             const box = e.currentTarget.getBoundingClientRect();
             const i = Math.round(((e.clientX - box.left) / box.width) * (points.length - 1));
             setHover(Math.max(0, Math.min(points.length - 1, i)));
           }}>
        {[0.25, 0.5, 0.75, 1].filter((v) => v > min).map((v) => (
          <line key={v} x1={P} x2={W - P} y1={y(v)} y2={y(v)} className="stroke-border" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        ))}
        <path d={line} fill="none" stroke={STATUS.synced.color} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        {hover !== null && (
          <>
            <line x1={x(hover)} x2={x(hover)} y1={P} y2={H - P} className="stroke-muted-foreground" strokeWidth="1" vectorEffect="non-scaling-stroke" />
            <circle cx={x(hover)} cy={y(points[hover].value)} r="4" fill={STATUS.synced.color} className="stroke-card" strokeWidth="2" />
          </>
        )}
      </svg>
    </div>
  );
}

/**
 * The library at a glance: a bar (or a ring) with a part for each status; a
 * part says what it is on hover and picks those songs on click; the legend
 * under it does the same. Then how it went, day by day.
 */
export function Coverage({ counts, chosen, onChoose, history }: {
  counts: Counts;
  chosen: LyricsStatus[];
  onChoose: (status: LyricsStatus) => void;
  history?: CoverageDay[];
}) {
  const [view, setView] = useState(readView);
  const total = STATUS_ORDER.reduce((sum, status) => sum + counts[status], 0);
  const scrolls = SCROLLS.reduce((sum, s) => sum + counts[s], 0);
  const turn = (next: "bar" | "donut") => {
    setView(next);
    try {
      localStorage.setItem(VIEW, next);
    } catch {
      // private window: not remembered
    }
  };

  const legend = (
    <ul className={cn("flex flex-wrap gap-x-1 gap-y-1", view === "donut" && "flex-col")}>
      {STATUS_ORDER.map((s) => (
        <li key={s}>
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" onClick={() => onChoose(s)} aria-pressed={chosen.includes(s)} disabled={!counts[s] && !chosen.includes(s)}
                      className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition hover:bg-accent disabled:opacity-40",
                                    chosen.includes(s) && "bg-accent text-foreground ring-1 ring-border")}>
                <span className="size-2 rounded-full" style={{ background: STATUS[s].color }} aria-hidden />
                {STATUS[s].label} <span className="text-muted-foreground tabular-nums">{counts[s].toLocaleString()}</span>
                <span className="text-muted-foreground/70 tabular-nums">{share(counts[s], total)}</span>
              </button>
            </TooltipTrigger>
            <TooltipContent>{STATUS[s].hint}</TooltipContent>
          </Tooltip>
        </li>
      ))}
    </ul>
  );

  return (
    <section className="rounded-2xl border border-border bg-card p-4 sm:p-5" aria-label="The library's coverage">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          <span className="text-2xl font-semibold text-foreground tabular-nums">{share(scrolls, total)}</span> of {total.toLocaleString()} songs
          scroll in a player
        </p>
        <div className="flex items-center gap-1" role="group" aria-label="Chart">
          {(["bar", "donut"] as const).map((v) => (
            <button key={v} type="button" onClick={() => turn(v)} aria-pressed={view === v} aria-label={v === "bar" ? "As a bar" : "As a ring"}
                    className={cn("rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground", view === v && "bg-accent text-foreground")}>
              {v === "bar" ? <ChartBarStacked className="size-4" /> : <ChartPie className="size-4" />}
            </button>
          ))}
        </div>
      </div>

      {view === "bar" ? (
        <>
          <div className="mt-3 flex h-3 gap-[2px] overflow-hidden rounded-full" role="img"
               aria-label={STATUS_ORDER.map((s) => `${STATUS[s].label} ${counts[s]}`).join(", ")}>
            {total === 0 && <span className="h-full w-full bg-muted" />}
            {STATUS_ORDER.filter((s) => counts[s]).map((s) => (
              <Tooltip key={s}>
                <TooltipTrigger asChild>
                  <button type="button" onClick={() => onChoose(s)} aria-label={`${STATUS[s].label}: ${counts[s]}`}
                          className={cn("h-full min-w-1 transition-opacity hover:brightness-125",
                                        chosen.length && !chosen.includes(s) && "opacity-30")}
                          style={{ width: `${(counts[s] / total) * 100}%`, background: STATUS[s].color }} />
                </TooltipTrigger>
                <TooltipContent><Tip status={s} count={counts[s]} total={total} /></TooltipContent>
              </Tooltip>
            ))}
          </div>
          <div className="mt-3">{legend}</div>
        </>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-6">
          <Donut counts={counts} total={total} chosen={chosen} onChoose={onChoose} />
          {legend}
        </div>
      )}

      {history && <div className="mt-5 border-t border-border pt-4"><History days={history} /></div>}
    </section>
  );
}
