import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { History, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { PageHeader } from "@/components/layout/PageHeader";
import { Empty, Failed } from "@/components/layout/States";
import { Pagination } from "@/components/scan/Pagination";
import { ScanCard } from "@/components/scan/ScanCard";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, errorText } from "@/lib/api";

function Skeleton() {
  return <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5" aria-hidden>{Array.from({ length: 10 }, (_, index) => <div key={index} className="aspect-[4/4] animate-pulse rounded-xl bg-card" />)}</div>;
}

export function ScansPage() {
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const [window_, setWindow] = useState({ size: 24, offset: 0 });
  useEffect(() => {
    const timer = window.setTimeout(() => { setQ(search.trim()); setWindow((value) => ({ ...value, offset: 0 })); }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  const params = { limit: window_.size, offset: window_.offset, q: q || undefined, status: status === "all" ? undefined : status };
  const scans = useQuery({ queryKey: ["projects", params], queryFn: () => api.projects(params), placeholderData: keepPreviousData });
  const total = scans.data?.total ?? 0;

  return (
    <>
      <PageHeader title="Scans" description="They stay here until you delete them, on every device you sign in on." actions={<Button asChild><Link to="/">New scan</Link></Button>} />
      <div className="mb-4 flex flex-wrap gap-2">
        <div className="relative min-w-52 flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input type="search" aria-label="Search by name" placeholder="Search by name" className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
        <Select value={status} onValueChange={(value) => { setStatus(value); setWindow((current) => ({ ...current, offset: 0 })); }}>
          <SelectTrigger aria-label="Show" className="w-40"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All scans</SelectItem>
            <SelectItem value="processing">In progress</SelectItem>
            <SelectItem value="ready">Finished</SelectItem>
            <SelectItem value="failed">With failed pages</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {scans.isPending ? <Skeleton /> : scans.error ? <Failed message={errorText(scans.error, "The scans couldn't be loaded.")} /> : !scans.data.items.length ? (
        <Empty icon={History} title={q || status !== "all" ? "No scan matches" : "No scans yet"}>
          {q || status !== "all" ? "Try another name or filter." : "Your scans will be here."}
        </Empty>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
            {scans.data.items.map((scan) => <ScanCard key={scan.public_id} scan={scan} />)}
          </div>
          <div className="mt-5"><Pagination total={total} size={window_.size} sizes={[24, 48, 96]} offset={window_.offset} noun="scans" onChange={(next) => { setWindow(next); window.scrollTo({ top: 0 }); }} /></div>
        </>
      )}
    </>
  );
}
