"use client"
import { useState, useEffect, useMemo } from "react"
import { PageSkeleton } from "@/components/skeleton"
import { useRouter } from "next/navigation"
import { getLeaderboard, removeFromLeaderboard } from "@/lib/api"
import { formatDate, formatAccuracy } from "@/lib/utils"
import { DataTable } from "@/components/data-table"
import FilterBar from "@/components/filter-bar"
import DropdownMenu from "@/components/dropdown-menu"
import EmptyState from "@/components/empty-state"

export default function LeaderboardPage() {
  const router = useRouter()
  const [entries, setEntries] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState("")
  const [providerFilter, setProviderFilter] = useState<string[]>([])
  const [benchmarkFilter, setBenchmarkFilter] = useState<string[]>([])

  const refresh = () => getLeaderboard().then(setEntries)

  useEffect(() => { refresh().finally(() => setLoading(false)) }, [])

  const sorted = useMemo(() =>
    [...entries].sort((a, b) => (b.accuracy || 0) - (a.accuracy || 0)),
    [entries]
  )

  const filtered = useMemo(() => sorted.filter((e) => {
    if (search && !e.provider?.toLowerCase().includes(search.toLowerCase()) && !e.version?.toLowerCase().includes(search.toLowerCase())) return false
    if (providerFilter.length && !providerFilter.includes(e.provider)) return false
    if (benchmarkFilter.length && !benchmarkFilter.includes(e.benchmark)) return false
    return true
  }), [sorted, search, providerFilter, benchmarkFilter])

  const toOptions = (key: string) => {
    const counts: Record<string, number> = {}
    for (const e of entries) { const v = e[key]; if (v) counts[v] = (counts[v] || 0) + 1 }
    return Object.entries(counts).map(([v, c]) => ({ value: v, label: v, count: c }))
  }

  const columns = [
    { key: "rank", header: "#", width: "48px", render: (_: any, i: number) => <span className="font-mono text-fg-muted">{i + 1}</span> },
    { key: "provider", header: "Provider", render: (e: any) => <span className="capitalize font-medium">{e.provider}</span> },
    { key: "benchmark", header: "Benchmark", render: (e: any) => <span className="capitalize">{e.benchmark}</span> },
    { key: "version", header: "Version", render: (e: any) => <span className="text-accent font-mono text-xs">{e.version}</span> },
    { key: "mode", header: "Mode", render: (e: any) => <span className="badge badge-neutral text-[10px]">{e.isolationMode || "global"}</span> },
    { key: "accuracy", header: "Accuracy", align: "right" as const, render: (e: any) => (
      <span className="font-mono font-semibold text-accent">{formatAccuracy(e.accuracy)}</span>
    )},
    { key: "questions", header: "Questions", align: "right" as const, render: (e: any) => (
      <span className="font-mono text-sm">{e.correctCount}/{e.totalQuestions}</span>
    )},
    { key: "date", header: "Added", render: (e: any) => <span className="text-xs text-fg-muted">{formatDate(e.addedAt)}</span> },
    { key: "actions", header: "", width: "48px", render: (e: any) => (
      <DropdownMenu items={[
        { label: "View details", onClick: () => router.push(`/leaderboard/${e.id}`) },
        { label: "View run", onClick: () => router.push(`/runs/${encodeURIComponent(e.runId)}`) },
        { label: "Remove", onClick: () => { if (confirm("Remove from leaderboard?")) removeFromLeaderboard(e.id).then(refresh) }, danger: true },
      ]} />
    )},
  ]

  if (loading) return <PageSkeleton />

  if (!entries.length) return (
    <div className="py-20">
      <EmptyState title="No leaderboard entries" description="Complete a benchmark run and add it to the leaderboard" />
    </div>
  )

  return (
    <div>
      <h1 className="text-xl font-display font-semibold mb-6">Leaderboard</h1>
      <FilterBar
        totalCount={entries.length} filteredCount={filtered.length}
        searchValue={search} onSearchChange={setSearch} searchPlaceholder="Search..."
        filters={[
          { key: "provider", label: "Provider", options: toOptions("provider"), selected: providerFilter, onChange: setProviderFilter },
          { key: "benchmark", label: "Benchmark", options: toOptions("benchmark"), selected: benchmarkFilter, onChange: setBenchmarkFilter },
        ]}
        onClearAll={() => { setSearch(""); setProviderFilter([]); setBenchmarkFilter([]) }}
      />
      <DataTable columns={columns} data={filtered} onRowClick={(e) => router.push(`/leaderboard/${e.id}`)} getRowKey={(e) => e.id} />
    </div>
  )
}
