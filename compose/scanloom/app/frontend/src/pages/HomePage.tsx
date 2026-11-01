import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Camera, Images, ScanLine } from "lucide-react";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { PROFILES } from "@/components/scan/labels";
import { ScanCard } from "@/components/scan/ScanCard";
import { Button } from "@/components/ui/button";
import { api, type Profile } from "@/lib/api";
import { saveProfile, savedProfile, useNewScan } from "@/lib/scanning";
import { ACCEPT } from "@/lib/uploads";
import { cn } from "@/lib/utils";

const Scanner = lazy(() => import("@/scanner/Scanner").then((module) => ({ default: module.Scanner })));

export function HomePage() {
  const { fromFiles, fromCamera, finishCamera } = useNewScan();
  const [camera, setCamera] = useState(false);
  const [over, setOver] = useState(false);
  const [profile, setProfile] = useState<Profile | undefined>(savedProfile);
  const input = useRef<HTMLInputElement>(null);
  const settings = useQuery({ queryKey: ["settings"], queryFn: api.settings, staleTime: 300_000 });
  const recent = useQuery({ queryKey: ["projects", { limit: 8, offset: 0 }], queryFn: () => api.projects({ limit: 8, offset: 0 }) });
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
    <>
      <h1 className="sr-only">New scan</h1>
      <section className={cn("flex flex-col items-center gap-5 rounded-2xl border-2 border-dashed px-5 py-10 text-center transition-colors sm:py-14", over ? "border-primary bg-primary/10" : "border-foreground/15 bg-card")}>
        <span className="flex size-14 items-center justify-center rounded-2xl bg-primary/15 text-primary"><ScanLine className="size-7" /></span>
        <div>
          <p className="text-xl font-semibold tracking-tight sm:text-2xl">Scan a document</p>
          <p className="mt-1 text-sm text-muted-foreground">Take pictures, or drop photos anywhere on this page. Scanning starts right away.</p>
        </div>
        <div className="flex w-full flex-col justify-center gap-2 sm:w-auto sm:flex-row">
          <Button size="lg" onClick={() => setCamera(true)}><Camera /> Use the camera</Button>
          <Button size="lg" variant="secondary" onClick={() => input.current?.click()}><Images /> Choose photos</Button>
          <input ref={input} hidden type="file" multiple accept={ACCEPT} onChange={(event) => { void fromFiles(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
        </div>
        <div className="flex flex-col items-center gap-1.5">
          <div role="radiogroup" aria-label="Finish" className="inline-flex rounded-lg border border-border bg-background p-0.5">
            {PROFILES.map(({ id, label, hint }) => (
              <button key={id} type="button" role="radio" aria-checked={chosen === id} title={hint}
                      onClick={() => { setProfile(id); saveProfile(id); }}
                      className={cn("min-h-9 rounded-md px-3 text-sm font-medium outline-none transition-colors focus-visible:ring-[3px] focus-visible:ring-ring", chosen === id ? "bg-primary text-primary-foreground hover:bg-primary-hover" : "text-muted-foreground hover:bg-accent hover:text-foreground")}>
                {label}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">{PROFILES.find(({ id }) => id === chosen)?.hint}. You can change it after.</p>
        </div>
      </section>

      {Boolean(recent.data?.items.length) && (
        <section className="mt-10">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-base font-semibold">Latest scans</h2>
            <Button asChild variant="ghost" size="sm"><Link to="/scans">All scans <ArrowRight /></Link></Button>
          </div>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4">
            {recent.data!.items.map((scan) => <ScanCard key={scan.public_id} scan={scan} />)}
          </div>
        </section>
      )}

      {camera && (
        <Suspense fallback={<div className="fixed inset-0 z-[70] bg-black" />}>
          <Scanner onCapture={(file, hint) => void fromCamera(file, hint)} onDone={(count) => { setCamera(false); void finishCamera(count); }} />
        </Suspense>
      )}
    </>
  );
}
