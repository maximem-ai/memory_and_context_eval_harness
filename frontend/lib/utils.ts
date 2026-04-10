export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ")
}

export function formatDate(iso: string): string {
  if (!iso) return "—"
  const d = new Date(iso)
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) + " " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" })
}

export function getStatusColor(status: string): string {
  switch (status) {
    case "completed": return "badge-success"
    case "running": case "initializing": case "pending": return "badge-running"
    case "failed": return "badge-error"
    case "partial": return "badge-warning"
    default: return "badge-neutral"
  }
}

export function formatNumber(n: number): string {
  return n.toLocaleString()
}

export function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function formatAccuracy(acc: number | null | undefined): string {
  if (acc == null) return "—"
  return `${acc.toFixed(1)}%`
}
