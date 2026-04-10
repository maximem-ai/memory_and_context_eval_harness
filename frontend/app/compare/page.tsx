"use client"
import { useState, useEffect, useCallback, useRef, useMemo } from "react"
import { PageSkeleton } from "@/components/skeleton"
import { useRouter } from "next/navigation"
import Link from "next/link"
import { getCompares, deleteCompare } from "@/lib/api"
import { formatDate, getStatusColor, cn } from "@/lib/utils"
import { DataTable } from "@/components/data-table"
import DropdownMenu from "@/components/dropdown-menu"
import EmptyState from "@/components/empty-state"

export default function ComparePage() {
  const router = useRouter()
  const [compares, setCompares] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const pollRef = useRef<NodeJS.Timeout | null>(null)

  const refresh = useCallback(async () => {
    try { setCompares(await getCompares()) } catch {}
  }, [])

  useEffect(() => { getCompares().then(setCompares).finally(() => setLoading(false)) }, [])

  const hasRunning = compares.some((c) => ["running", "pending"].includes(c.status))
  useEffect(() => {
    if (hasRunning) pollRef.current = setInterval(refresh, 2000)
    else if (pollRef.current) clearInterval(pollRef.current)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [hasRunning, refresh])

  const columns = [
    { key: "id", header: "Compare ID", render: (c: any) => <span className="font-mono text-xs text-accent">{c.compareId}</span> },
    { key: "providers", header: "Providers", render: (c: any) => (
      <div className="flex flex-wrap gap-1">{(c.providers || []).map((p: string) => <span key={p} className="badge badge-neutral text-[10px]">{p}</span>)}</div>
    )},
    { key: "benchmark", header: "Benchmark", render: (c: any) => <span className="capitalize">{c.benchmark}</span> },
    { key: "status", header: "Status", render: (c: any) => <span className={cn("badge", getStatusColor(c.status))}>{c.status}</span> },
    { key: "date", header: "Date", render: (c: any) => <span className="text-fg-muted text-xs">{formatDate(c.createdAt)}</span> },
    { key: "actions", header: "", width: "48px", render: (c: any) => (
      <DropdownMenu items={[
        { label: "View details", onClick: () => router.push(`/compare/${encodeURIComponent(c.compareId)}`) },
        { label: "Delete", onClick: () => { if (confirm("Delete?")) deleteCompare(c.compareId).then(refresh) }, danger: true },
      ]} />
    )},
  ]

  if (loading) return <PageSkeleton />

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-display font-semibold">Comparisons</h1>
        <Link href="/compare/new" className="btn btn-primary">New Comparison</Link>
      </div>
      {!compares.length ? (
        <EmptyState title="No comparisons yet" description="Compare multiple providers against the same benchmark">
          <Link href="/compare/new" className="btn btn-primary mt-4">New Comparison</Link>
        </EmptyState>
      ) : (
        <DataTable columns={columns} data={compares} onRowClick={(c) => router.push(`/compare/${encodeURIComponent(c.compareId)}`)} getRowKey={(c) => c.compareId} />
      )}
    </div>
  )
}
