const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8766"

export interface RunSummary {
  run_id: string
  provider: string
  benchmark: string
  status: string
  isolation_mode: string
  created_at: string
  updated_at: string
}

export interface RunDetail extends RunSummary {
  judge: string
  answeringModel: string
  isolationMode: string
  accuracy: number | null
  summary: { total: number; searched: number; answered: number; evaluated: number }
  questions: Record<string, QuestionCheckpoint>
}

export interface QuestionCheckpoint {
  questionId: string
  containerTag: string
  question: string
  groundTruth: string
  questionType: string
  questionDate?: string
  searchResults?: any[]
  phases: { search: PhaseData; answer: PhaseData; evaluate: PhaseData }
}

export interface PhaseData {
  status: string
  durationMs?: number
  error?: string
  results?: any[]
  resultCount?: number
  hypothesis?: string
  score?: number
  label?: string
  explanation?: string
  retrieval_metrics?: any
}

async function fetchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${endpoint}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    throw new Error(`API error ${res.status}: ${text}`)
  }
  if (res.status === 204) return undefined as T
  return res.json()
}

// Runs
export const getRuns = () => fetchApi<RunSummary[]>("/api/runs")
export const getRun = (runId: string) => fetchApi<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`)
export const getRunReport = (runId: string) => fetchApi<any>(`/api/report/${encodeURIComponent(runId)}`)
export const getQuestion = (runId: string, qId: string) => fetchApi<QuestionCheckpoint>(`/api/runs/${encodeURIComponent(runId)}/questions/${encodeURIComponent(qId)}`)
export const deleteRun = (runId: string) => fetchApi<void>(`/api/runs/${encodeURIComponent(runId)}`, { method: "DELETE" })
export const stopRun = (runId: string) => fetchApi<void>(`/api/runs/${encodeURIComponent(runId)}/stop`, { method: "POST" })

export const startRun = (params: Record<string, any>) =>
  fetchApi<any>("/api/run", { method: "POST", body: JSON.stringify(params) })

export const resetAndRerun = (runId: string, fromPhase: string) =>
  fetchApi<any>(`/api/runs/${encodeURIComponent(runId)}/reset-phase`, { method: "POST", body: JSON.stringify({ from_phase: fromPhase }) })

// Providers & Benchmarks
export const getProviders = () => fetchApi<string[]>("/api/providers")
export const getBenchmarks = () => fetchApi<string[]>("/api/benchmarks")

// Ingest
export const startIngest = (params: Record<string, any>) =>
  fetchApi<any>("/api/ingest", { method: "POST", body: JSON.stringify(params) })
export const stopIngest = (params: Record<string, any>) =>
  fetchApi<any>("/api/ingest/stop", { method: "POST", body: JSON.stringify(params) })
export const getIngestStatus = (benchmark: string) =>
  fetchApi<any>(`/api/ingest/status/${encodeURIComponent(benchmark)}`)

// Comparisons
export const getCompares = () => fetchApi<any[]>("/api/compare")
export const getCompare = (id: string) => fetchApi<any>(`/api/compare/${encodeURIComponent(id)}`)
export const getCompareReport = (id: string) => fetchApi<any>(`/api/compare/${encodeURIComponent(id)}/report`)
export const startCompare = (params: Record<string, any>) =>
  fetchApi<any>("/api/compare", { method: "POST", body: JSON.stringify(params) })
export const stopCompare = (id: string) => fetchApi<void>(`/api/compare/${encodeURIComponent(id)}/stop`, { method: "POST" })
export const deleteCompare = (id: string) => fetchApi<void>(`/api/compare/${encodeURIComponent(id)}`, { method: "DELETE" })

// Leaderboard
export const getLeaderboard = () => fetchApi<any[]>("/api/leaderboard")
export const getLeaderboardEntry = (id: number | string) => fetchApi<any>(`/api/leaderboard/${id}`)
export const addToLeaderboard = (runId: string, version: string, notes: string) =>
  fetchApi<any>("/api/leaderboard", { method: "POST", body: JSON.stringify({ run_id: runId, version, notes }) })
export const removeFromLeaderboard = (id: number | string) => fetchApi<void>(`/api/leaderboard/${id}`, { method: "DELETE" })
