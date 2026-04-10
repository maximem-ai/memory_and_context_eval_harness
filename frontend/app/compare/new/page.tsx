"use client"
import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { getProviders, getBenchmarks, startCompare } from "@/lib/api"
import SingleSelect from "@/components/single-select"

const MODELS = [
  { value: "gpt-4o", label: "GPT-4o" },
  { value: "gpt-4o-mini", label: "GPT-4o Mini" },
  { value: "o4-mini", label: "o4-mini" },
  { value: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
  { value: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
  { value: "gpt-5-mini", label: "GPT-5 Mini" },
]

export default function NewComparePage() {
  const router = useRouter()
  const [providers, setProviders] = useState<string[]>([])
  const [benchmarks, setBenchmarks] = useState<string[]>([])
  const [selectedProviders, setSelectedProviders] = useState<string[]>([])
  const [benchmark, setBenchmark] = useState("")
  const [isolationMode, setIsolationMode] = useState("global")
  const [judgeModel, setJudgeModel] = useState("gpt-4o")
  const [answeringModel, setAnsweringModel] = useState("gpt-4o")
  const [limit, setLimit] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => { getProviders().then(setProviders); getBenchmarks().then(setBenchmarks) }, [])

  const toggleProvider = (p: string) => {
    setSelectedProviders((prev) => prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p])
  }

  const handleSubmit = async () => {
    if (selectedProviders.length < 2) { setError("Select at least 2 providers"); return }
    if (!benchmark) { setError("Select a benchmark"); return }
    setSubmitting(true); setError("")
    try {
      await startCompare({
        providers: selectedProviders, benchmark,
        judge_model: judgeModel, answering_model: answeringModel,
        isolation_mode: isolationMode,
        limit: limit ? parseInt(limit) : undefined,
      })
      router.push("/compare")
    } catch (e: any) { setError(e.message || "Failed") } finally { setSubmitting(false) }
  }

  return (
    <div className="max-w-3xl">
      <h1 className="text-xl font-display font-semibold mb-6">New Comparison</h1>
      <div className="card space-y-5">
        <div>
          <label className="block text-xs text-fg-muted mb-2 font-medium">Providers (select 2+)</label>
          <div className="flex flex-wrap gap-2">
            {providers.map((p) => (
              <button key={p} onClick={() => toggleProvider(p)}
                className={`px-3 py-1.5 rounded-md border text-sm transition-all ${
                  selectedProviders.includes(p) ? "border-accent bg-accent/20 text-accent" : "border-line bg-bg-elevated text-fg-muted hover:border-line-hover"
                }`}>{p}</button>
            ))}
          </div>
          {selectedProviders.length > 0 && <div className="text-xs text-fg-muted mt-1">{selectedProviders.length} selected</div>}
        </div>

        <SingleSelect label="Benchmark" options={benchmarks.map((b) => ({ value: b, label: b }))} value={benchmark} onChange={setBenchmark} />

        <SingleSelect label="Isolation Mode"
          options={[
            { value: "global", label: "Global", sublabel: "Shared container" },
            { value: "isolated", label: "Isolated", sublabel: "Per-question container" },
          ]}
          value={isolationMode} onChange={setIsolationMode}
        />

        <div className="grid grid-cols-2 gap-4">
          <SingleSelect label="Judge Model" options={MODELS} value={judgeModel} onChange={setJudgeModel} />
          <SingleSelect label="Answering Model" options={MODELS} value={answeringModel} onChange={setAnsweringModel} />
        </div>

        <div>
          <label className="block text-xs text-fg-muted mb-1.5 font-medium">Question Limit (optional)</label>
          <input type="number" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="All" className="input w-40 text-sm" />
        </div>

        {error && <div className="text-sm text-red-400 bg-red-900/10 rounded px-3 py-2">{error}</div>}

        <div className="flex items-center gap-3 pt-2">
          <button onClick={handleSubmit} disabled={submitting} className="btn btn-primary">{submitting ? "Starting..." : "Start Comparison"}</button>
          <button onClick={() => router.back()} className="btn btn-ghost">Cancel</button>
        </div>
      </div>
    </div>
  )
}
