"use client"
import { useState, useEffect, useCallback, useRef, useMemo } from "react"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { getRuns, deleteRun, stopRun } from "@/lib/api"
import { formatDate, formatAccuracy, getStatusColor, cn } from "@/lib/utils"
import { DataTable } from "@/components/data-table"
import FilterBar from "@/components/filter-bar"
import CircularProgress from "@/components/circular-progress"
import DropdownMenu from "@/components/dropdown-menu"
import EmptyState from "@/components/empty-state"
import { TableSkeleton } from "@/components/skeleton"

export default function RunsPage() {
  const router = useRouter()
  const [runs, setRuns] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState("")
  const [providerFilter, setProviderFilter] = useState<string[]>([])
  const [benchmarkFilter, setBenchmarkFilter] = useState<string[]>([])
  const [statusFilter, setStatusFilter] = useState<string[]>([])
  const pollRef = useRef<NodeJS.Timeout | null>(null)

  const refresh = useCallback(async () => {
    try { setRuns(await getRuns()) } catch {}
  }, [])

  useEffect(() => {
    getRuns().then(setRuns).finally(() => setLoading(false))
  }, [])

  const hasRunning = runs.some((r) => ["running", "initializing", "pending"].includes(r.status))

  useEffect(() => {
    if (hasRunning) { pollRef.current = setInterval(refresh, 2000) }
    else if (pollRef.current) { clearInterval(pollRef.current) }
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [hasRunning, refresh])

  const filtered = useMemo(() => runs.filter((r) => {
    if (search && !r.run_id?.toLowerCase().includes(search.toLowerCase())) return false
    if (providerFilter.length && !providerFilter.includes(r.provider)) return false
    if (benchmarkFilter.length && !benchmarkFilter.includes(r.benchmark)) return false
    if (statusFilter.length && !statusFilter.includes(r.status)) return false
    return true
  }), [runs, search, providerFilter, benchmarkFilter, statusFilter])

  const toOptions = (key: string) => {
    const counts: Record<string, number> = {}
    for (const r of runs) { const v = r[key]; if (v) counts[v] = (counts[v] || 0) + 1 }
    return Object.entries(counts).map(([v, c]) => ({ value: v, label: v, count: c }))
  }

  const columns = [
    { key: "run_id", header: "Run ID", render: (r: any) => <span className="font-mono text-xs text-accent">{r.run_id}</span> },
    { key: "provider", header: "Provider", render: (r: any) => <span className="capitalize">{r.provider}</span> },
    { key: "benchmark", header: "Benchmark", render: (r: any) => <span className="capitalize">{r.benchmark}</span> },
    { key: "status", header: "Status", render: (r: any) => (
      <div className="flex items-center gap-2">
        <span className={cn("badge", getStatusColor(r.status))}>{r.status}</span>
        {["running", "initializing"].includes(r.status) && <CircularProgress progress={0.5} />}
      </div>
    )},
    { key: "isolation", header: "Mode", render: (r: any) => <span className="badge badge-neutral text-[10px]">{r.isolation_mode || "global"}</span> },
    { key: "date", header: "Date", render: (r: any) => <span className="text-fg-muted text-xs">{formatDate(r.created_at)}</span> },
    { key: "actions", header: "", width: "48px", render: (r: any) => (
      <DropdownMenu items={[
        { label: "View details", onClick: () => router.push(`/runs/${encodeURIComponent(r.run_id)}`) },
        { label: "Stop", onClick: () => stopRun(r.run_id).then(refresh), disabled: !["running", "initializing"].includes(r.status) },
        { label: "Delete", onClick: () => { if (confirm("Delete this run?")) deleteRun(r.run_id).then(refresh) }, danger: true },
      ]} />
    )},
  ]

  if (loading) return <TableSkeleton rows={6} />

  if (!runs.length) return (
    <div className="py-20">
      <EmptyState title="No runs yet" description="Start a new benchmark run to see results here">
        <Link href="/runs/new" className="btn btn-primary mt-4">New Run</Link>
      </EmptyState>
    </div>
  )

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-display font-semibold">Runs</h1>
        <Link href="/runs/new" className="btn btn-primary">New Run</Link>
      </div>
      <FilterBar
        totalCount={runs.length} filteredCount={filtered.length}
        searchValue={search} onSearchChange={setSearch} searchPlaceholder="Search runs..."
        filters={[
          { key: "provider", label: "Provider", options: toOptions("provider"), selected: providerFilter, onChange: setProviderFilter },
          { key: "benchmark", label: "Benchmark", options: toOptions("benchmark"), selected: benchmarkFilter, onChange: setBenchmarkFilter },
          { key: "status", label: "Status", options: toOptions("status"), selected: statusFilter, onChange: setStatusFilter },
        ]}
        onClearAll={() => { setSearch(""); setProviderFilter([]); setBenchmarkFilter([]); setStatusFilter([]) }}
      />
      <DataTable
        columns={columns} data={filtered}
        onRowClick={(r) => router.push(`/runs/${encodeURIComponent(r.run_id)}`)}
        getRowKey={(r) => r.run_id}
      />
    </div>
  )
}
