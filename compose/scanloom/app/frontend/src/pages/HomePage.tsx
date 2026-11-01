import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Camera, Check, ImagePlus, ShieldCheck, Upload } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";

import { PROFILES } from "@/components/scan/labels";
import { LookSample } from "@/components/scan/LookSample";
import { api, type Profile } from "@/lib/api";
import { saveProfile, savedProfile, useNewScan } from "@/lib/scanning";
import { ACCEPT } from "@/lib/uploads";
import { cn } from "@/lib/utils";

const Scanner = lazy(() => import("@/scanner/Scanner").then((module) => ({ default: module.Scanner })));

/** A page under a scanner's beam: what this screen is for, at a glance. */
function ScanArt() {
  return (
    <div aria-hidden className="relative mx-auto h-44 w-36 shrink-0" style={{ ["--beam" as string]: "160px" }}>
      <div className="absolute inset-3 rotate-[-5deg] rounded-xl bg-white/5" />
      <LookSample look="paper" className="absolute inset-0 size-full rotate-[2deg] drop-shadow-2xl" />
      <div className="scan-beam absolute inset-x-1 top-1 h-1 rounded-full bg-primary shadow-[0_0_24px_6px_var(--primary)]" />
    </div>
  );
}

export function HomePage() {
  const { fromFiles, fromCamera, finishCamera } = useNewScan();
  const [camera, setCamera] = useState(false);
  const [over, setOver] = useState(false);
  const [profile, setProfile] = useState<Profile | undefined>(savedProfile);
  const input = useRef<HTMLInputElement>(null);
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 300_000 });
  const chosen = profile ?? settings.data?.values.default_profile ?? "faithful";

  // A drop anywhere on the page counts.
  useEffect(() => {
    const hasFiles = (event: DragEvent) => event.dataTransfer?.types.includes("Files");
    const enter = (event: DragEvent) => { if (hasFiles(event)) { event.preventDefault(); setOver(true); } };
    const leave = (event: DragEvent) => { if (!event.relatedTarget) setOver(false); };
    const drop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      setOver(false);
      void fromFiles(Array.from(event.dataTransfer!.files));
    };
    window.addEventListener("dragover", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragover", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [fromFiles]);

  return (
    <div className="flex flex-col gap-4">
      <header className="mb-2">
        <h1 className="text-4xl font-semibold tracking-tight text-balance sm:text-5xl">Scan a document</h1>
        <p className="mt-2 flex items-center gap-2 text-base text-muted-foreground"><ShieldCheck className="size-4 shrink-0 text-primary" /> You see your photos first. Nothing is scanned until you say.</p>
      </header>

      <div className="grid gap-4 md:grid-cols-5">
        <button type="button" onClick={() => setCamera(true)}
                className="tile tile-lift group flex min-h-56 flex-col justify-between rounded-3xl p-6 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring md:col-span-3"
                style={{ background: "linear-gradient(150deg, color-mix(in oklch, var(--primary) 30%, transparent), oklch(1 0 0 / 0.03) 70%)" }}>
          <div className="flex items-start justify-between gap-4">
            <span className="flex size-14 items-center justify-center rounded-2xl bg-primary text-primary-foreground"><Camera className="size-7" /></span>
            <ScanArt />
          </div>
          <span className="mt-2 flex items-end justify-between gap-3">
            <span>
              <span className="block text-2xl font-semibold tracking-tight">Use the camera</span>
              <span className="block text-sm text-muted-foreground">Hold a page in view: it takes the picture by itself.</span>
            </span>
            <ArrowRight className="size-6 shrink-0 transition-transform group-hover:translate-x-1" />
          </span>
        </button>

        <button type="button" onClick={() => input.current?.click()}
                className={cn("tile tile-lift group flex min-h-56 flex-col justify-between rounded-3xl border-dashed p-6 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring md:col-span-2", over && "border-primary bg-primary/15")}>
          <span className="flex size-14 items-center justify-center rounded-2xl bg-white/10 text-foreground"><ImagePlus className="size-7" /></span>
          <span>
            <span className="block text-2xl font-semibold tracking-tight">{over ? "Drop them here" : "Choose photos"}</span>
            <span className="mt-1 flex items-center gap-1.5 text-sm text-muted-foreground"><Upload className="size-3.5 shrink-0" /> Or drop them anywhere. JPEG, PNG, WebP, HEIC.</span>
          </span>
        </button>
        <input ref={input} hidden type="file" multiple accept={ACCEPT} onChange={(event) => { void fromFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
      </div>

      <section aria-labelledby="look" className="tile rounded-3xl p-5 sm:p-6">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-x-4">
          <h2 id="look" className="text-lg font-semibold tracking-tight">How should the pages look?</h2>
          <p className="text-sm text-muted-foreground">You can change it after, for the whole scan or one page.</p>
        </div>
        <div role="radiogroup" aria-labelledby="look" className="grid gap-3 sm:grid-cols-3">
          {PROFILES.map(({ id, label, hint }) => {
            const on = chosen === id;
            return (
              <button key={id} type="button" role="radio" aria-checked={on} onClick={() => { setProfile(id); saveProfile(id); }}
                      className={cn("group flex items-center gap-4 rounded-2xl border p-3 text-left outline-none transition-all focus-visible:ring-[3px] focus-visible:ring-ring sm:flex-col sm:items-stretch sm:p-4",
                                    on ? "border-primary bg-primary/10 shadow-[0_0_0_1px_var(--primary)]" : "border-border hover:border-foreground/30 hover:bg-white/5")}>
                <span className={cn("flex h-24 w-20 shrink-0 items-center justify-center rounded-xl bg-black/30 sm:h-40 sm:w-full")}>
                  <LookSample look={id} className={cn("h-[85%] drop-shadow-lg transition-transform duration-200 group-hover:scale-105", on && "scale-105")} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-2 font-medium">
                    {label}
                    <span className={cn("flex size-5 shrink-0 items-center justify-center rounded-full border", on ? "border-primary bg-primary text-primary-foreground" : "border-foreground/30")}>{on && <Check className="size-3.5" strokeWidth={3} />}</span>
                  </span>
                  <span className="mt-0.5 block text-sm text-muted-foreground">{hint}</span>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {camera && (
        <Suspense fallback={<div className="fixed inset-0 z-[70] bg-black" />}>
          <Scanner onCapture={(file, hint) => void fromCamera(file, hint)} onDone={(count) => { setCamera(false); void finishCamera(count); }} />
        </Suspense>
      )}
    </div>
  );
}
