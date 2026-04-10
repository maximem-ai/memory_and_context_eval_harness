"use client"
import { useState, useEffect } from "react"
import { PageSkeleton } from "@/components/skeleton"
import { useParams } from "next/navigation"
import Link from "next/link"
import { getLeaderboardEntry } from "@/lib/api"
import { formatDate, formatAccuracy } from "@/lib/utils"
import { StatsGrid, AccuracyByType, LatencyTable, RetrievalMetrics } from "@/components/benchmark-results"

export default function LeaderboardEntryPage() {
  const params = useParams()
  const id = parseInt(params.id as string)
  const [entry, setEntry] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getLeaderboardEntry(id).then(setEntry).finally(() => setLoading(false))
  }, [id])

  if (loading) return <PageSkeleton />
  if (!entry) return <div className="text-fg-muted py-20 text-center">Entry not found</div>

  return (
    <div>
      <div className="flex items-center gap-2 text-sm text-fg-muted mb-4">
        <Link href="/leaderboard" className="hover:text-fg">Leaderboard</Link>
        <span>/</span>
        <span className="text-fg">{entry.provider} v{entry.version}</span>
      </div>

      <div className="flex items-center gap-3 mb-6">
        <h1 className="text-xl font-display font-semibold capitalize">{entry.provider}</h1>
        <span className="badge badge-neutral">v{entry.version}</span>
        <span className="badge badge-neutral capitalize">{entry.benchmark}</span>
        <span className="badge badge-neutral text-[10px]">{entry.isolationMode || "global"}</span>
      </div>

      <div className="flex items-center gap-6 text-sm text-fg-muted mb-6">
        <span>Judge: <strong className="text-fg">{entry.judgeModel}</strong></span>
        <span>Model: <strong className="text-fg">{entry.answeringModel}</strong></span>
        <span>Added: {formatDate(entry.addedAt)}</span>
        {entry.notes && <span>Notes: <em className="text-fg-secondary">{entry.notes}</em></span>}
      </div>

      <div className="space-y-6">
        <StatsGrid cards={[
          { label: "Accuracy", value: formatAccuracy(entry.accuracy), mono: true },
          { label: "Questions", value: `${entry.correctCount}/${entry.totalQuestions}` },
          { label: "Judge", value: entry.judgeModel || "—" },
          { label: "Model", value: entry.answeringModel || "—" },
        ]} />

        {entry.byQuestionType && <AccuracyByType byQuestionType={entry.byQuestionType} />}
        {entry.latency && <LatencyTable latency={entry.latency} />}
        {entry.retrieval && <RetrievalMetrics retrieval={entry.retrieval} />}
      </div>

      <div className="mt-6">
        <Link href={`/runs/${encodeURIComponent(entry.runId)}`} className="text-sm text-accent hover:text-accent-hover">
          View original run &rarr;
        </Link>
      </div>
    </div>
  )
}
