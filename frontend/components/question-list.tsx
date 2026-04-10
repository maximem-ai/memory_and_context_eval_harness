"use client"
import { useState, useMemo } from "react"
import Link from "next/link"
import { cn, getStatusColor } from "@/lib/utils"

interface QuestionCheckpoint {
  questionId: string; containerTag: string; question: string; groundTruth: string
  questionType: string; phases: { search: any; answer: any; evaluate: any }
}

interface QuestionListProps {
  runId: string
  questions: Record<string, QuestionCheckpoint>
}

export default function QuestionList({ runId, questions }: QuestionListProps) {
  const [search, setSearch] = useState("")
  const [typeFilter, setTypeFilter] = useState("")
  const [failuresOnly, setFailuresOnly] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)

  const entries = useMemo(() => {
    return Object.values(questions).filter((q) => {
      if (search && !q.question.toLowerCase().includes(search.toLowerCase()) && !q.questionId.toLowerCase().includes(search.toLowerCase())) return false
      if (typeFilter && q.questionType !== typeFilter) return false
      if (failuresOnly && q.phases?.evaluate?.label !== "incorrect") return false
      return true
    })
  }, [questions, search, typeFilter, failuresOnly])

  const types = [...new Set(Object.values(questions).map((q) => q.questionType))].sort()
  const failCount = Object.values(questions).filter((q) => q.phases?.evaluate?.label === "incorrect").length

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <input
          type="text" value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder="Search questions..." className="input text-sm w-64"
        />
        <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="select text-sm">
          <option value="">All types</option>
          {types.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <button
          onClick={() => setFailuresOnly(!failuresOnly)}
          className={cn("btn text-xs", failuresOnly ? "btn-danger" : "btn-ghost")}
        >
          Failures ({failCount})
        </button>
        <span className="text-xs text-fg-muted ml-auto">{entries.length} questions</span>
      </div>

      <div className="border border-line rounded-lg overflow-hidden">
        {entries.map((q) => {
          const evalPhase = q.phases?.evaluate || {}
          const isCorrect = evalPhase.label === "correct"
          const isExpanded = expanded === q.questionId

          return (
            <div key={q.questionId} className="border-b border-line/50 last:border-0">
              <button
                onClick={() => setExpanded(isExpanded ? null : q.questionId)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-bg-elevated/50 transition-colors"
              >
                <span className={`w-2 h-2 rounded-full shrink-0 ${evalPhase.status === "completed" ? (isCorrect ? "bg-emerald-400" : "bg-red-400") : "bg-gray-500"}`} />
                <span className="font-mono text-xs text-fg-muted w-24 shrink-0 truncate">{q.questionId}</span>
                <span className="badge badge-neutral text-[10px]">{q.questionType}</span>
                <span className="text-sm text-fg truncate flex-1">{q.question}</span>
                {evalPhase.label && (
                  <span className={cn("badge text-[10px]", isCorrect ? "badge-success" : "badge-error")}>{evalPhase.label}</span>
                )}
                <svg className={`w-4 h-4 text-fg-muted transition-transform ${isExpanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 16 16">
                  <path d="M4 6l4 4 4-4" stroke="currentColor" strokeWidth="1.5" />
                </svg>
              </button>

              {isExpanded && (
                <div className="px-4 pb-4 space-y-3 bg-bg-surface/50">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="text-xs text-fg-muted mb-1">Ground Truth</div>
                      <div className="text-sm text-fg bg-bg-elevated rounded p-3">{q.groundTruth}</div>
                    </div>
                    <div>
                      <div className="text-xs text-fg-muted mb-1">Model Answer</div>
                      <div className={`text-sm rounded p-3 ${isCorrect ? "bg-emerald-900/20 text-emerald-200" : "bg-red-900/20 text-red-200"}`}>
                        {q.phases?.answer?.hypothesis || "—"}
                      </div>
                    </div>
                  </div>
                  {evalPhase.explanation && (
                    <div>
                      <div className="text-xs text-fg-muted mb-1">Explanation</div>
                      <div className="text-sm text-fg-secondary">{evalPhase.explanation}</div>
                    </div>
                  )}
                  <Link href={`/runs/${encodeURIComponent(runId)}/questions/${encodeURIComponent(q.questionId)}`} className="text-xs text-accent hover:text-accent-hover">
                    View full details &rarr;
                  </Link>
                </div>
              )}
            </div>
          )
        })}
        {entries.length === 0 && (
          <div className="px-4 py-8 text-center text-fg-muted text-sm">No questions match your filters</div>
        )}
      </div>
    </div>
  )
}
