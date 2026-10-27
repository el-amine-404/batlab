export function Logo({ compact = false }: { compact?: boolean }) {
  return <span className="brand"><svg viewBox="0 0 64 64" aria-hidden="true"><rect width="64" height="64" rx="16" fill="currentColor" opacity=".12"/><path d="M17 26V17h9M38 17h9v9M47 38v9h-9M26 47h-9v-9" fill="none" stroke="currentColor" strokeWidth="4" strokeLinecap="round"/><path d="M23 25c7-5 12 5 18 0v14c-7 5-12-5-18 0z" className="brand-page"/></svg>{!compact && <span>Scanloom</span>}</span>;
}
