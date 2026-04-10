"use client"
import { useState, useEffect, useCallback, useRef } from "react"
import { PageSkeleton } from "@/components/skeleton"
import { useParams } from "next/navigation"
import Link from "next/link"
import { getCompare, getCompareReport, stopCompare } from "@/lib/api"
import { formatDate, getStatusColor, cn, formatAccuracy } from "@/lib/utils"
import { StatsGrid, LatencyTable, RetrievalMetrics } from "@/components/benchmark-results"
import AccuracyBarChart from "@/components/accuracy-bar-chart"

export default function CompareDetailPage() {
  const params = useParams()
  const compareId = decodeURIComponent(params.compareId as string)
  const [comp, setComp] = useState<any>(null)
  const [reports, setReports] = useState<Record<string, any>>({})
  const [loading, setLoading] = useState(true)
  const pollRef = useRef<NodeJS.Timeout | null>(null)

  const refresh = useCallback(async () => {
    try {
      const c = await getCompare(compareId)
      setComp(c)
      if (c.status === "completed") {
        try {
          const r = await getCompareReport(compareId)
          setReports(r.reports || {})
        } catch {}
      }
    } catch {}
  }, [compareId])

  useEffect(() => { refresh().finally(() => setLoading(false)) }, [refresh])

  const isRunning = comp && ["running", "pending"].includes(comp.status)
  useEffect(() => {
    if (isRunning) pollRef.current = setInterval(refresh, 2000)
    else if (pollRef.current) clearInterval(pollRef.current)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [isRunning, refresh])

  if (loading) return <PageSkeleton />
  if (!comp) return <div className="text-fg-muted py-20 text-center">Comparison not found</div>

  const providers = comp.providers || []
  const reportEntries = Object.entries(reports) as [string, any][]

  // Build chart data
  const allTypes = new Set<string>()
  for (const [, r] of reportEntries) {
    for (const t of Object.keys(r.by_question_type || {})) allTypes.add(t)
  }
  const chartData = [...allTypes].map((type) => ({
    type,
    values: providers.map((p: string) => ({
      provider: p,
      accuracy: reports[p]?.by_question_type?.[type]?.accuracy,
    })),
  }))

  return (
    <div>
      <div className="flex items-center gap-2 text-sm text-fg-muted mb-4">
        <Link href="/compare" className="hover:text-fg">Comparisons</Link>
        <span>/</span>
        <span className="text-fg font-mono">{compareId}</span>
      </div>

      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-display font-semibold">{compareId}</h1>
          <span className={cn("badge", getStatusColor(comp.status))}>{comp.status}</span>
        </div>
        {isRunning && <button onClick={() => stopCompare(compareId).then(refresh)} className="btn btn-danger text-xs">Stop</button>}
      </div>

      <div className="flex items-center gap-6 text-sm text-fg-muted mb-6">
        <span>Providers: {providers.map((p: string) => <span key={p} className="badge badge-neutral text-[10px] ml-1">{p}</span>)}</span>
        <span>Benchmark: <strong className="text-fg capitalize">{comp.benchmark}</strong></span>
        <span>Mode: <strong className="text-fg">{comp.isolationMode || "global"}</strong></span>
      </div>

      {/* Per-provider run links */}
      {comp.runs && Object.keys(comp.runs).length > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-6">
          {Object.entries(comp.runs).map(([pname, runId]: [string, any]) => (
            <Link key={pname} href={`/runs/${encodeURIComponent(runId)}`} className="card-hover block">
              <div className="text-sm font-medium capitalize">{pname}</div>
              <div className="text-xs font-mono text-fg-muted truncate">{runId}</div>
            </Link>
          ))}
        </div>
      )}

      {/* Results */}
      {reportEntries.length > 0 && (
        <div className="space-y-6">
          {/* Accuracy comparison */}
          <div>
            <h3 className="text-sm font-medium text-fg-secondary mb-3">Overall Accuracy</h3>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              {reportEntries.map(([pname, r]) => {
                const acc = r.summary?.accuracy ?? r.summary?.correct_count / r.summary?.total_questions * 100
                return (
                  <div key={pname} className="card text-center">
                    <div className="text-xs text-fg-muted mb-1 capitalize">{pname}</div>
                    <div className="text-2xl font-semibold font-mono text-fg">{formatAccuracy(acc)}</div>
                    <div className="text-[10px] text-fg-muted">{r.summary?.correct_count ?? 0}/{r.summary?.total_questions ?? 0}</div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Bar chart */}
          {chartData.length > 0 && <AccuracyBarChart data={chartData} providers={providers} />}

          {/* Latency comparison */}
          {reportEntries.some(([, r]) => r.latency) && (
            <div>
              <h3 className="text-sm font-medium text-fg-secondary mb-3">Latency Comparison (median ms)</h3>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-line">
                      <th className="table-header">Provider</th>
                      <th className="table-header text-right">Search</th>
                      <th className="table-header text-right">Answer</th>
                      <th className="table-header text-right">Evaluate</th>
                      <th className="table-header text-right">Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reportEntries.map(([pname, r]) => (
                      <tr key={pname} className="border-b border-line/50">
                        <td className="table-cell capitalize">{pname}</td>
                        <td className="table-cell text-right font-mono">{r.latency?.search?.median?.toFixed(0) ?? "—"}</td>
                        <td className="table-cell text-right font-mono">{r.latency?.answer?.median?.toFixed(0) ?? "—"}</td>
                        <td className="table-cell text-right font-mono">{r.latency?.evaluate?.median?.toFixed(0) ?? "—"}</td>
                        <td className="table-cell text-right font-mono">{r.latency?.total?.median?.toFixed(0) ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {reportEntries.length === 0 && !isRunning && (
        <div className="text-fg-muted text-sm py-12 text-center">No reports available yet.</div>
      )}
    </div>
  )
}
