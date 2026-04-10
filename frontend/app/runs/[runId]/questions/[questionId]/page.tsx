"use client"
import { useState, useEffect } from "react"
import { useParams } from "next/navigation"
import Link from "next/link"
import { getQuestion } from "@/lib/api"
import { cn, formatMs } from "@/lib/utils"
import CopyButton from "@/components/copy-button"
import { PageSkeleton } from "@/components/skeleton"

export default function QuestionDetailPage() {
  const params = useParams()
  const runId = decodeURIComponent(params.runId as string)
  const questionId = decodeURIComponent(params.questionId as string)
  const [q, setQ] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    getQuestion(runId, questionId).then(setQ).finally(() => setLoading(false))
  }, [runId, questionId])

  if (loading) return <PageSkeleton />
  if (!q) return <div className="text-fg-muted py-20 text-center">Question not found</div>

  const evalPhase = q.phases?.evaluate || {}
  const answerPhase = q.phases?.answer || {}
  const searchPhase = q.phases?.search || {}
  const isCorrect = evalPhase.label === "correct"

  return (
    <div>
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-fg-muted mb-4">
        <Link href="/runs" className="hover:text-fg transition-colors">Runs</Link>
        <span>/</span>
        <Link href={`/runs/${encodeURIComponent(runId)}`} className="hover:text-fg transition-colors font-mono text-xs" title={runId}>{runId.length > 30 ? runId.slice(0, 30) + "..." : runId}</Link>
        <span>/</span>
        <span className="text-fg font-mono text-xs">{questionId}</span>
      </div>

      {/* Header */}
      <div className="flex items-center gap-3 mb-4">
        <h1 className="text-lg font-display font-semibold">{questionId}</h1>
        <CopyButton text={questionId} />
        <span className="badge badge-neutral">{q.questionType}</span>
        {evalPhase.label && <span className={cn("badge", isCorrect ? "badge-success" : "badge-error")}>{evalPhase.label}</span>}
        {evalPhase.score != null && <span className="text-xs font-mono text-fg-muted">score: {evalPhase.score.toFixed(2)}</span>}
      </div>

      {/* Container tag */}
      <div className="flex items-center gap-2 text-xs text-fg-muted mb-6">
        <span>Container:</span>
        <code className="font-mono text-fg-secondary bg-bg-elevated px-1.5 py-0.5 rounded">{q.containerTag}</code>
        <CopyButton text={q.containerTag} />
      </div>

      {/* Question */}
      <div className="card mb-4">
        <div className="text-xs text-fg-muted mb-2">Question</div>
        <div className="text-sm text-fg">{q.question}</div>
        {q.questionDate && <div className="text-xs text-fg-muted mt-2">Date: {q.questionDate}</div>}
      </div>

      {/* Answer comparison */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div className="card">
          <div className="text-xs text-fg-muted mb-2">Ground Truth</div>
          <div className="text-sm text-fg bg-bg-elevated rounded p-3">{q.groundTruth}</div>
        </div>
        <div className={cn("card border", isCorrect ? "border-emerald-600/30" : "border-red-600/30")}>
          <div className="text-xs text-fg-muted mb-2">Model Answer</div>
          <div className={cn("text-sm rounded p-3", isCorrect ? "bg-emerald-900/20 text-emerald-200" : "bg-red-900/20 text-red-200")}>
            {answerPhase.hypothesis || "—"}
          </div>
        </div>
      </div>

      {/* Explanation */}
      {evalPhase.explanation && (
        <div className="card mb-4">
          <div className="text-xs text-fg-muted mb-2">Judge Explanation</div>
          <div className="text-sm text-fg-secondary">{evalPhase.explanation}</div>
        </div>
      )}

      {/* Phase timeline */}
      <div className="card mb-4">
        <div className="text-xs text-fg-muted mb-3">Phase Timeline</div>
        <div className="grid grid-cols-3 gap-4">
          {[
            { name: "Search", phase: searchPhase },
            { name: "Answer", phase: answerPhase },
            { name: "Evaluate", phase: evalPhase },
          ].map(({ name, phase }) => (
            <div key={name} className="flex items-center gap-3">
              <div className={cn("w-2 h-2 rounded-full", phase.status === "completed" ? "bg-success" : "bg-fg-muted/30")} />
              <div>
                <div className="text-sm font-medium">{name}</div>
                <div className="text-xs text-fg-muted">
                  {phase.status === "completed" && phase.durationMs ? formatMs(phase.durationMs) : phase.status || "pending"}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Retrieved context */}
      {q.searchResults && q.searchResults.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between mb-3">
            <div className="text-xs text-fg-muted">Retrieved Context ({q.searchResults.length} results)</div>
          </div>
          <div className="space-y-2 max-h-[600px] overflow-y-auto">
            {q.searchResults.map((result: any, i: number) => (
              <div key={i} className="bg-bg-elevated rounded-lg p-3 text-xs">
                <div className="flex items-center gap-2 mb-2">
                  <span className="font-mono text-accent font-semibold">#{i + 1}</span>
                  {result.score != null && <span className="text-fg-muted">score: {typeof result.score === "number" ? result.score.toFixed(3) : result.score}</span>}
                  {result.context_type && <span className="badge badge-neutral text-[9px]">{result.context_type}</span>}
                  <div className="flex-1" />
                  <CopyButton text={JSON.stringify(result, null, 2)} />
                </div>
                {result.memory ? (
                  <div className="text-fg-secondary">{result.memory}</div>
                ) : (
                  <pre className="text-fg-secondary whitespace-pre-wrap font-mono text-[11px] leading-relaxed">
                    {JSON.stringify(result, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
