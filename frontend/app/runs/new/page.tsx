"use client"
import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { getProviders, getBenchmarks, startRun, startIngest } from "@/lib/api"
import SingleSelect from "@/components/single-select"

const MODELS = [
  { value: "gpt-4o", label: "GPT-4o" },
  { value: "gpt-4o-mini", label: "GPT-4o Mini" },
  { value: "o4-mini", label: "o4-mini" },
  { value: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
  { value: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
  { value: "gpt-5-mini", label: "GPT-5 Mini" },
]

export default function NewRunPage() {
  const router = useRouter()
  const [providers, setProviders] = useState<string[]>([])
  const [benchmarks, setBenchmarks] = useState<string[]>([])
  const [provider, setProvider] = useState("")
  const [benchmark, setBenchmark] = useState("")
  const [isolationMode, setIsolationMode] = useState("global")
  const [judgeModel, setJudgeModel] = useState("gpt-4o")
  const [answeringModel, setAnsweringModel] = useState("gpt-4o")
  const [limit, setLimit] = useState("")
  const [retrievalMode, setRetrievalMode] = useState("accurate")
  const [containerTagPrefix, setContainerTagPrefix] = useState("")
  const [concurrency, setConcurrency] = useState("5")
  const [ingestFirst, setIngestFirst] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    getProviders().then(setProviders)
    getBenchmarks().then(setBenchmarks)
  }, [])

  const handleSubmit = async () => {
    if (!provider || !benchmark) { setError("Provider and benchmark are required"); return }
    setSubmitting(true); setError("")
    try {
      if (ingestFirst) {
        await startIngest({ provider, benchmark, isolation_mode: isolationMode })
      }
      const result = await startRun({
        provider, benchmark, judge_model: judgeModel, answering_model: answeringModel,
        isolation_mode: isolationMode,
        retrieval_mode: provider === "synap" ? retrievalMode : undefined,
        container_tag_prefix: containerTagPrefix || undefined,
        limit: limit ? parseInt(limit) : undefined,
        concurrency: concurrency ? parseInt(concurrency) : undefined,
      })
      const runId = result?.runId
      if (runId) {
        router.push(`/runs/${encodeURIComponent(runId)}`)
      } else {
        router.push("/runs")
      }
    } catch (e: any) {
      setError(e.message || "Failed to start run")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-display font-semibold mb-6">New Run</h1>

      <div className="card space-y-5">
        <div className="grid grid-cols-2 gap-4">
          <SingleSelect label="Provider" options={providers.map((p) => ({ value: p, label: p }))} value={provider} onChange={setProvider} placeholder="Select provider" />
          <SingleSelect label="Benchmark" options={benchmarks.map((b) => ({ value: b, label: b }))} value={benchmark} onChange={setBenchmark} placeholder="Select benchmark" />
        </div>

        <SingleSelect
          label="Isolation Mode"
          options={[
            { value: "global", label: "Global", sublabel: "Shared container for all sessions — fast, cross-session reasoning" },
            { value: "isolated", label: "Isolated", sublabel: "Separate container per question — clean evaluation, no cross-contamination" },
          ]}
          value={isolationMode} onChange={setIsolationMode}
        />

        <div className={`grid gap-4 ${provider === "synap" ? "grid-cols-3" : "grid-cols-2"}`}>
          <SingleSelect label="Judge Model" options={MODELS} value={judgeModel} onChange={setJudgeModel} />
          <SingleSelect label="Answering Model" options={MODELS} value={answeringModel} onChange={setAnsweringModel} />
          {provider === "synap" && (
            <SingleSelect
              label="Retrieval Mode"
              options={[
                { value: "accurate", label: "Accurate", sublabel: "Full search — slower but higher quality" },
                { value: "fast", label: "Fast", sublabel: "Cache-first — faster but may miss recent data" },
              ]}
              value={retrievalMode} onChange={setRetrievalMode}
            />
          )}
        </div>

        <div>
          <label className="block text-xs text-fg-muted mb-1.5 font-medium">Container Tag / Entity ID (optional)</label>
          <input type="text" value={containerTagPrefix} onChange={(e) => setContainerTagPrefix(e.target.value)} placeholder={`${benchmark || "benchmark"}-${provider || "provider"}`} className="input w-full text-sm font-mono" />
          <p className="text-[10px] text-fg-muted mt-1">
            {isolationMode === "isolated"
              ? <span>Per-question container: <code className="text-fg-secondary">{containerTagPrefix || `${benchmark || "benchmark"}-${provider || "provider"}`}_{"{question_id}"}</code></span>
              : <span>Leave empty to use default: <code className="text-fg-secondary">{`${benchmark || "benchmark"}-${provider || "provider"}`}</code></span>
            }
          </p>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-fg-muted mb-1.5 font-medium">Question Limit (optional)</label>
            <input type="number" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="All questions" className="input w-full text-sm" />
          </div>
          <div>
            <label className="block text-xs text-fg-muted mb-1.5 font-medium">Concurrency</label>
            <input type="number" value={concurrency} onChange={(e) => setConcurrency(e.target.value)} className="input w-full text-sm" />
          </div>
        </div>

        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={ingestFirst} onChange={(e) => setIngestFirst(e.target.checked)} className="w-4 h-4 rounded border-line bg-bg-elevated accent-accent" />
          <span className="text-sm text-fg-secondary">Run ingestion before evaluation</span>
        </label>

        {error && <div className="text-sm text-red-400 bg-red-900/10 rounded px-3 py-2">{error}</div>}

        <div className="flex items-center gap-3 pt-2">
          <button onClick={handleSubmit} disabled={submitting} className="btn btn-primary">
            {submitting ? "Starting..." : "Start Run"}
          </button>
          <button onClick={() => router.back()} className="btn btn-ghost">Cancel</button>
        </div>
      </div>
    </div>
  )
}
