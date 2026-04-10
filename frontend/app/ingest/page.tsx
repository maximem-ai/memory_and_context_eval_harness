"use client"
import { useState, useEffect, useCallback, useRef } from "react"
import { getProviders, getBenchmarks, getIngestStatus, startIngest, stopIngest } from "@/lib/api"
import SingleSelect from "@/components/single-select"
import { formatNumber } from "@/lib/utils"

export default function IngestPage() {
  const [providers, setProviders] = useState<string[]>([])
  const [benchmarks, setBenchmarks] = useState<string[]>([])
  const [provider, setProvider] = useState("")
  const [benchmark, setBenchmark] = useState("")
  const [isolationMode, setIsolationMode] = useState("global")
  const [containerTagPrefix, setContainerTagPrefix] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [activeIngest, setActiveIngest] = useState<{ provider: string; benchmark: string } | null>(null)
  const [statuses, setStatuses] = useState<Record<string, any>>({})
  const pollRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    getProviders().then(setProviders)
    getBenchmarks().then(setBenchmarks)
  }, [])

  const refreshStatus = useCallback(async () => {
    const newStatuses: Record<string, any> = {}
    for (const b of benchmarks) {
      try {
        newStatuses[b] = await getIngestStatus(b)
      } catch {}
    }
    setStatuses((prev) => ({ ...prev, ...newStatuses }))
  }, [benchmarks])

  useEffect(() => {
    if (benchmarks.length) refreshStatus()
  }, [benchmarks, refreshStatus])

  // Always poll while there's an active ingest or in-flight items
  const hasActive = activeIngest !== null || Object.values(statuses).some((s: any) =>
    Object.values(s?.providers || {}).some((p: any) => p.in_flight > 0)
  )

  useEffect(() => {
    if (hasActive) {
      pollRef.current = setInterval(refreshStatus, 2000)
    } else if (pollRef.current) {
      clearInterval(pollRef.current)
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [hasActive, refreshStatus])

  const handleIngest = async () => {
    if (!provider || !benchmark) return
    setSubmitting(true)
    setActiveIngest({ provider, benchmark })
    try {
      await startIngest({
        provider, benchmark, isolation_mode: isolationMode,
        container_tag_prefix: isolationMode === "isolated" && containerTagPrefix ? containerTagPrefix : undefined,
      })
      // Start polling immediately
      refreshStatus()
    } catch (e: any) {
      alert(e.message || "Ingest failed")
      setActiveIngest(null)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div>
      <h1 className="text-xl font-display font-semibold mb-6">Ingest Data</h1>

      <div className="card mb-6 max-w-2xl">
        <div className="grid grid-cols-2 gap-4 mb-4">
          <SingleSelect label="Provider" options={providers.map((p) => ({ value: p, label: p }))} value={provider} onChange={setProvider} placeholder="Select provider" />
          <SingleSelect label="Benchmark" options={benchmarks.map((b) => ({ value: b, label: b }))} value={benchmark} onChange={setBenchmark} placeholder="Select benchmark" />
        </div>
        <SingleSelect
          label="Isolation Mode"
          options={[
            { value: "global", label: "Global", sublabel: "All sessions in one shared container" },
            { value: "isolated", label: "Isolated", sublabel: "Separate container per question (slower)" },
          ]}
          value={isolationMode} onChange={setIsolationMode}
        />
        {isolationMode === "isolated" && (
          <>
            <div className="text-xs text-warning bg-warning/10 rounded px-3 py-2 mt-3">
              Isolated mode creates a separate container per question. This is significantly slower but ensures each question only sees its relevant sessions.
            </div>
            <div className="mt-3">
              <label className="block text-xs text-fg-muted mb-1.5 font-medium">Container Tag Prefix (optional)</label>
              <input type="text" value={containerTagPrefix} onChange={(e) => setContainerTagPrefix(e.target.value)} placeholder={`${benchmark || "benchmark"}-${provider || "provider"}`} className="input w-full text-sm font-mono" />
              <p className="text-[10px] text-fg-muted mt-1">Per-question container: <code className="text-fg-secondary">{`{prefix}_{question_id}`}</code>. Use to match existing ingested data.</p>
            </div>
          </>
        )}
        <button onClick={handleIngest} disabled={submitting || !provider || !benchmark} className="btn btn-primary mt-4">
          {submitting ? "Starting..." : "Start Ingestion"}
        </button>
      </div>

      {/* Active ingest banner */}
      {activeIngest && (
        <div className="mb-6 bg-accent/10 border border-accent/30 rounded-lg px-4 py-3 flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-accent animate-pulse" />
          <div className="flex-1">
            <div className="text-sm text-fg font-medium">
              Ingesting {activeIngest.benchmark} into {activeIngest.provider}...
            </div>
            <div className="text-xs text-fg-secondary mt-0.5">
              This may take a while. Progress updates below.
            </div>
          </div>
          <button
            onClick={() => {
              stopIngest({ provider: activeIngest.provider, benchmark: activeIngest.benchmark }).catch(() => {})
              setActiveIngest(null)
              refreshStatus()
            }}
            className="btn btn-danger text-xs"
          >
            Stop
          </button>
        </div>
      )}

      <h2 className="text-lg font-display font-semibold mb-4">Ingestion Status</h2>

      {benchmarks.map((b) => {
        const status = statuses[b]
        const providerEntries = Object.entries(status?.providers || {})
        if (!providerEntries.length) return null

        return (
          <div key={b} className="mb-6">
            <h3 className="text-sm font-medium text-fg-secondary mb-3 capitalize">{b}</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
              {providerEntries.map(([pname, pdata]: [string, any]) => {
                const isRunning = pdata.in_flight > 0 || (activeIngest?.provider === pname && activeIngest?.benchmark === b)
                return (
                  <div key={pname} className="card">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-sm font-medium capitalize">{pname.split(":")[0]}</span>
                      {isRunning && (
                        <span className="badge badge-running text-[10px] flex items-center gap-1">
                          <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse" />
                          Running
                        </span>
                      )}
                      {!isRunning && pdata.completed_turns > 0 && <span className="badge badge-success text-[10px]">Ready</span>}
                    </div>
                    <div className="space-y-1 text-xs text-fg-muted">
                      <div>Questions covered: <span className="text-fg font-mono">{pdata.questions_covered}</span></div>
                      <div>Turns ingested: <span className="text-fg font-mono">{formatNumber(pdata.completed_turns)}</span></div>
                      {pdata.container_tag && <div className="truncate" title={pdata.container_tag}>Container: <code className="text-fg-secondary">{pdata.container_tag}</code></div>}
                    </div>
                    {isRunning && (
                      <button
                        onClick={() => {
                          stopIngest({ provider: pname, benchmark: b }).catch(() => {})
                          if (activeIngest?.provider === pname) setActiveIngest(null)
                          refreshStatus()
                        }}
                        className="btn btn-danger text-xs mt-2 w-full"
                      >
                        Stop
                      </button>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}

      {!benchmarks.some((b) => Object.keys(statuses[b]?.providers || {}).length > 0) && !activeIngest && (
        <div className="text-fg-muted text-sm py-8 text-center">No ingestion data yet. Start an ingestion above.</div>
      )}
    </div>
  )
}
