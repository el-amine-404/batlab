import { Cpu, LayoutDashboard, Library, ListMusic, LogOut, Settings, Sparkles } from "lucide-react"
import { NavLink, Outlet, useLocation } from "react-router-dom"
import { Separator } from "@/components/ui/separator"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarHeader, SidebarInset,
  SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger,
} from "@/components/ui/sidebar"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { api } from "@/lib/api"
import { useStatus } from "@/lib/status"
import { cn } from "@/lib/utils"

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/library", label: "Library", icon: Library },
  { to: "/queue", label: "AI queue", icon: ListMusic },
  { to: "/workers", label: "Workers", icon: Cpu },
  { to: "/settings", label: "Settings", icon: Settings },
]

function Logo() {
  return (
    <div className="flex items-center gap-2.5 px-2 py-1.5">
      <div className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-sky-400 to-violet-500 text-white shadow-sm">
        <Sparkles className="size-4" />
      </div>
      <div className="leading-tight group-data-[collapsible=icon]:hidden">
        <div className="font-semibold tracking-tight">Stanzarr</div>
        <div className="text-xs text-muted-foreground">Lyrics for your library</div>
      </div>
    </div>
  )
}

/** What the server is doing, in the top bar: background work and the GPU. */
function Activity() {
  const { status } = useStatus()
  if (!status) return null
  return (
    <div className="ml-auto flex items-center gap-4 text-xs text-muted-foreground">
      {status.background && (
        <span className="flex items-center gap-2"><span className="size-1.5 animate-pulse rounded-full bg-sky-400" />{status.background}…</span>
      )}
      {status.read_only && <span className="rounded border px-1.5 py-0.5">read-only</span>}
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex items-center gap-2">
            <span className={cn("size-1.5 rounded-full", status.gpu_online ? "bg-emerald-500" : "bg-zinc-600")} />
            {status.gpu_online ? "GPU worker online" : "No GPU worker"}
          </span>
        </TooltipTrigger>
        <TooltipContent>{status.gpu_online ? "AI sync is available" : "AI sync needs a worker with a GPU: see Workers"}</TooltipContent>
      </Tooltip>
    </div>
  )
}

export function Layout({ user, version, onSignOut }: { user: string; version: string; onSignOut: () => void }) {
  const { pathname } = useLocation()
  const current = NAV.find((n) => (n.to === "/" ? pathname === "/" : pathname.startsWith(n.to)))
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader><Logo /></SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV.map((n) => (
                  <SidebarMenuItem key={n.to}>
                    <SidebarMenuButton asChild isActive={n === current} tooltip={n.label}>
                      <NavLink to={n.to} end={n.to === "/"}><n.icon />{n.label}</NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem>
              <SidebarMenuButton tooltip="Sign out" onClick={() => api("/api/logout", { method: "POST" }).then(onSignOut)}>
                <LogOut />
                <span className="flex-1 truncate">{user}</span>
                <span className="text-xs text-muted-foreground">v{version}</span>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
      </Sidebar>
      <SidebarInset className="min-w-0">
        <header className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-3 border-b bg-background/80 px-4 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="h-4" />
          <h1 className="text-sm font-medium">{current?.label ?? "Stanzarr"}</h1>
          <Activity />
        </header>
        <main className="flex-1 p-6"><Outlet /></main>
      </SidebarInset>
    </SidebarProvider>
  )
}
