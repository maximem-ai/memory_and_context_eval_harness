"use client"
import { useState, useEffect, useCallback, useRef } from "react"
import { useParams, useSearchParams } from "next/navigation"
import Link from "next/link"
import { getRun, getRunReport, stopRun, startRun, resetAndRerun } from "@/lib/api"
import { formatDate, formatAccuracy, getStatusColor, cn } from "@/lib/utils"
import { StatsGrid, AccuracyByType, LatencyTable, RetrievalMetrics } from "@/components/benchmark-results"
import PhaseProgress from "@/components/phase-progress"
import QuestionList from "@/components/question-list"
import CopyButton from "@/components/copy-button"
import { PageSkeleton } from "@/components/skeleton"

export default function RunDetailPage() {
  const params = useParams()
  const searchParams = useSearchParams()
  const runId = decodeURIComponent(params.runId as string)
  const [tab, setTab] = useState(searchParams.get("tab") || "overview")
  const [run, setRun] = useState<any>(null)
  const [report, setReport] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [showModelPicker, setShowModelPicker] = useState<"answer" | "evaluate" | null>(null)
  const [pickerJudge, setPickerJudge] = useState("")
  const [pickerModel, setPickerModel] = useState("")
  const pollRef = useRef<NodeJS.Timeout | null>(null)

  const refresh = useCallback(async () => {
    try {
      const r = await getRun(runId)
      setRun(r)
      try { setReport(await getRunReport(runId)) } catch {}
    } catch {}
  }, [runId])

  useEffect(() => {
    refresh().finally(() => setLoading(false))
  }, [refresh])

  const isRunning = run && ["running", "initializing", "pending"].includes(run.status)

  useEffect(() => {
    if (isRunning) pollRef.current = setInterval(refresh, 2000)
    else if (pollRef.current) clearInterval(pollRef.current)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [isRunning, refresh])

  if (loading) return <PageSkeleton />
  if (!run) return <div className="text-fg-muted py-20 text-center">Run not found</div>

  const summary = run.summary || { total: 0, searched: 0, answered: 0, evaluated: 0 }
  const questionCount = Object.keys(run.questions || {}).length
  const evaluated = Object.values(run.questions || {}).filter((q: any) => q.phases?.evaluate?.status === "completed")
  const correct = evaluated.filter((q: any) => q.phases?.evaluate?.label === "correct").length
  const accuracy = evaluated.length > 0 ? (correct / evaluated.length * 100) : null

  const byType: Record<string, any> = {}
  if (report?.by_question_type) {
    for (const [t, s] of Object.entries(report.by_question_type as Record<string, any>)) {
      byType[t] = s
    }
  } else {
    for (const q of Object.values(run.questions || {}) as any[]) {
      const t = q.questionType || "unknown"
      if (!byType[t]) byType[t] = { correct: 0, total: 0 }
      byType[t].total++
      if (q.phases?.evaluate?.label === "correct") byType[t].correct++
      byType[t].accuracy = byType[t].total > 0 ? (byType[t].correct / byType[t].total * 100) : 0
    }
  }

  return (
    <div>
      <div className="flex items-center gap-2 text-sm text-fg-muted mb-4">
        <Link href="/runs" className="hover:text-fg">Runs</Link>
        <span>/</span>
        <span className="text-fg font-mono">{runId}</span>
      </div>

      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-display font-semibold">{runId}</h1>
          <CopyButton text={runId} />
          <span className={cn("badge", getStatusColor(run.status))}>{run.status}</span>
          <span className="badge badge-neutral text-[10px]">{run.isolationMode || "global"}</span>
        </div>
        <div className="flex items-center gap-2">
          {isRunning && (
            <button onClick={() => stopRun(runId).catch(() => {}).finally(refresh)} className="btn btn-danger text-xs">Stop</button>
          )}
          {!isRunning && summary.searched > 0 && summary.answered < summary.total && (
            <button onClick={() => startRun({ run_id: runId, provider: run.provider, benchmark: run.benchmark, judge_model: run.judge, answering_model: run.answeringModel, isolation_mode: run.isolationMode, phases: ["answer", "evaluate", "report"] }).catch(() => {}).finally(refresh)} className="btn btn-primary text-xs">
              Continue: Answer + Evaluate
            </button>
          )}
          {!isRunning && summary.answered > 0 && summary.evaluated < summary.total && (
            <button onClick={() => startRun({ run_id: runId, provider: run.provider, benchmark: run.benchmark, judge_model: run.judge, answering_model: run.answeringModel, isolation_mode: run.isolationMode, phases: ["evaluate", "report"] }).catch(() => {}).finally(refresh)} className="btn btn-primary text-xs">
              Continue: Evaluate
            </button>
          )}
          {!isRunning && summary.evaluated > 0 && summary.evaluated === summary.total && run.status !== "completed" && (
            <button onClick={() => startRun({ run_id: runId, provider: run.provider, benchmark: run.benchmark, judge_model: run.judge, answering_model: run.answeringModel, isolation_mode: run.isolationMode, phases: ["report"] }).catch(() => {}).finally(refresh)} className="btn btn-primary text-xs">
              Generate Report
            </button>
          )}
          {!isRunning && summary.evaluated > 0 && (
            <button onClick={() => { setShowModelPicker("evaluate"); setPickerJudge(run.judge || "gpt-4o"); setPickerModel(run.answeringModel || "gpt-4o") }} className="btn btn-secondary text-xs">
              Re-evaluate
            </button>
          )}
          {!isRunning && summary.answered > 0 && (
            <button onClick={() => { setShowModelPicker("answer"); setPickerJudge(run.judge || "gpt-4o"); setPickerModel(run.answeringModel || "gpt-4o") }} className="btn btn-ghost text-xs">
              Re-answer + Evaluate
            </button>
          )}
          {!isRunning && (
            <button onClick={() => startRun({ run_id: runId, provider: run.provider, benchmark: run.benchmark, judge_model: run.judge, answering_model: run.answeringModel, isolation_mode: run.isolationMode, phases: ["search", "answer", "evaluate", "report"], force: true }).catch(() => {}).finally(refresh)} className="btn btn-ghost text-xs">
              Re-run All
            </button>
          )}
        </div>
      </div>

      {/* Model picker for re-runs */}
      {showModelPicker && (
        <div className="mb-4 bg-bg-surface border border-line rounded-lg p-4 animate-fade-in">
          <div className="flex items-center justify-between mb-3">
            <span className="text-sm font-medium text-fg">
              {showModelPicker === "evaluate" ? "Re-evaluate with different models" : "Re-answer + Evaluate with different models"}
            </span>
            <button onClick={() => setShowModelPicker(null)} className="text-fg-muted hover:text-fg text-xs">Cancel</button>
          </div>
          <div className="flex items-end gap-3">
            {showModelPicker === "answer" && (
              <div className="flex-1">
                <label className="block text-xs text-fg-muted mb-1">Answering Model</label>
                <select value={pickerModel} onChange={(e) => setPickerModel(e.target.value)} className="select w-full text-sm">
                  {["gpt-4o", "gpt-4o-mini", "o4-mini", "gpt-5-mini", "gemini-2.5-flash", "gemini-2.5-pro"].map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
            )}
            <div className="flex-1">
              <label className="block text-xs text-fg-muted mb-1">Judge Model</label>
              <select value={pickerJudge} onChange={(e) => setPickerJudge(e.target.value)} className="select w-full text-sm">
                {["gpt-4o", "gpt-4o-mini", "o4-mini", "gpt-5-mini", "gemini-2.5-flash", "gemini-2.5-pro"].map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            <button
              onClick={() => {
                const opts = { judgeModel: pickerJudge, answeringModel: pickerModel }
                resetAndRerun(runId, showModelPicker, opts).catch(() => {}).finally(() => { setShowModelPicker(null); refresh() })
              }}
              className="btn btn-primary text-xs whitespace-nowrap"
            >
              {showModelPicker === "evaluate" ? "Re-evaluate" : "Re-answer + Evaluate"}
            </button>
          </div>
        </div>
      )}

      <div className="flex items-center gap-6 text-sm text-fg-muted mb-6">
        <span>Provider: <strong className="text-fg capitalize">{run.provider}</strong></span>
        <span>Benchmark: <strong className="text-fg capitalize">{run.benchmark}</strong></span>
        <span>Judge: <strong className="text-fg">{run.judge}</strong></span>
        <span>Model: <strong className="text-fg">{run.answeringModel}</strong></span>
        <span>Created: {formatDate(run.createdAt)}</span>
      </div>

      <PhaseProgress summary={summary} />

      <div className="flex gap-1 border-b border-line mt-6 mb-6">
        {["overview", "results"].map((t) => (
          <button key={t} onClick={() => setTab(t)}
            className={cn("px-4 py-2 text-sm font-medium border-b-2 transition-colors",
              tab === t ? "border-accent text-accent" : "border-transparent text-fg-muted hover:text-fg"
            )}>
            {t === "overview" ? "Overview" : `Results (${evaluated.length})`}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <div className="space-y-6">
          <StatsGrid cards={[
            { label: "Accuracy", value: accuracy != null ? `${accuracy.toFixed(1)}%` : "—", mono: true },
            { label: "Questions", value: `${evaluated.length}/${questionCount}`, subtext: "evaluated" },
            { label: "Judge", value: run.judge || "—" },
            { label: "Model", value: run.answeringModel || "—" },
          ]} />
          <AccuracyByType byQuestionType={byType} />
          {report?.latency && <LatencyTable latency={report.latency} />}
          {report?.retrieval && <RetrievalMetrics retrieval={report.retrieval} />}
        </div>
      )}

      {tab === "results" && (
        <QuestionList runId={runId} questions={run.questions || {}} />
      )}
    </div>
  )
}
