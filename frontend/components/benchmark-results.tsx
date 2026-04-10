"use client"
import { formatMs } from "@/lib/utils"

// ── StatCard & StatsGrid ────────────────────────────────────────

interface StatCardProps { label: string; value: string | number; subtext?: string; mono?: boolean }

export function StatCard({ label, value, subtext, mono }: StatCardProps) {
  return (
    <div className="card text-center">
      <div className="text-[10px] uppercase tracking-wider text-fg-muted mb-1">{label}</div>
      <div className={`text-2xl font-semibold text-fg ${mono ? "font-mono" : ""}`}>{value}</div>
      {subtext && <div className="text-xs text-fg-muted mt-0.5">{subtext}</div>}
    </div>
  )
}

export function StatsGrid({ cards }: { cards: StatCardProps[] }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {cards.map((c, i) => <StatCard key={i} {...c} />)}
    </div>
  )
}

// ── AccuracyByType ──────────────────────────────────────────────

interface TypeStats { accuracy?: number; correct?: number; total?: number }

export function AccuracyByType({ byQuestionType }: { byQuestionType: Record<string, TypeStats> }) {
  const entries = Object.entries(byQuestionType)
  if (!entries.length) return null

  return (
    <div>
      <h3 className="text-sm font-medium text-fg-secondary mb-3">Accuracy by Question Type</h3>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
        {entries.map(([type, stats]) => (
          <div key={type} className="card">
            <div className="text-xs text-fg-muted mb-1">{type.replace(/[-_]/g, " ")}</div>
            <div className="text-xl font-semibold font-mono text-fg">
              {stats.accuracy != null ? `${stats.accuracy.toFixed(1)}%` : "—"}
            </div>
            <div className="text-[10px] text-fg-muted">
              {stats.correct ?? 0}/{stats.total ?? 0} correct
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── LatencyTable ────────────────────────────────────────────────

interface LatencyRow { min?: number; max?: number; mean?: number; median?: number; p95?: number; p99?: number; count?: number }

export function LatencyTable({ latency }: { latency: Record<string, LatencyRow> }) {
  const phases = ["search", "answer", "evaluate", "total"]
  const cols = ["min", "max", "mean", "median", "p95", "p99"] as const

  return (
    <div>
      <h3 className="text-sm font-medium text-fg-secondary mb-3">Latency (ms)</h3>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-line">
              <th className="table-header">Phase</th>
              {cols.map((c) => <th key={c} className="table-header text-right">{c.toUpperCase()}</th>)}
            </tr>
          </thead>
          <tbody>
            {phases.map((phase) => {
              const row = latency[phase]
              if (!row) return null
              return (
                <tr key={phase} className="border-b border-line/50">
                  <td className="table-cell capitalize">{phase}</td>
                  {cols.map((c) => (
                    <td key={c} className="table-cell text-right font-mono text-sm">
                      {row[c] != null ? formatMs(row[c]!) : "—"}
                    </td>
                  ))}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── RetrievalMetrics ────────────────────────────────────────────

interface RetrievalData {
  hit_at_k?: number; precision_at_k?: number; recall_at_k?: number
  f1_at_k?: number; mrr?: number; ndcg?: number; k?: number
}

const METRIC_TOOLTIPS: Record<string, string> = {
  "Hit@K": "Whether at least one relevant result appears in the top-K retrieved items. 1 = found, 0 = missed.",
  "Precision@K": "Fraction of the top-K retrieved items that are actually relevant to the question.",
  "Recall@K": "Fraction of all relevant items that were successfully retrieved in the top-K.",
  "F1@K": "Harmonic mean of Precision and Recall. Balances both into a single score.",
  "MRR": "Mean Reciprocal Rank — average of 1/position of the first relevant result. Higher means relevant results appear earlier.",
  "NDCG": "Normalized Discounted Cumulative Gain — measures ranking quality, giving more credit to relevant results appearing higher in the list.",
}

export function RetrievalMetrics({ retrieval }: { retrieval: RetrievalData | null }) {
  if (!retrieval) return null

  const fmt = (v: number | undefined) => {
    if (v == null) return "—"
    return v <= 1 ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(1)}%`
  }

  const metrics = [
    { label: "Hit@K", value: retrieval.hit_at_k },
    { label: "Precision@K", value: retrieval.precision_at_k },
    { label: "Recall@K", value: retrieval.recall_at_k },
    { label: "F1@K", value: retrieval.f1_at_k },
    { label: "MRR", value: retrieval.mrr },
    { label: "NDCG", value: retrieval.ndcg },
  ]

  return (
    <div>
      <h3 className="text-sm font-medium text-fg-secondary mb-3">
        Retrieval Metrics {retrieval.k ? `(K=${retrieval.k})` : ""}
      </h3>
      <div className="grid grid-cols-3 lg:grid-cols-6 gap-3">
        {metrics.map((m) => (
          <div key={m.label} className="card text-center group relative">
            <div className="text-[10px] uppercase tracking-wider text-fg-muted mb-1 flex items-center justify-center gap-1">
              {m.label}
              <svg className="w-3 h-3 text-fg-muted/50 group-hover:text-fg-muted" viewBox="0 0 16 16" fill="currentColor">
                <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="1.2" />
                <text x="8" y="11.5" textAnchor="middle" fontSize="9" fontWeight="600">?</text>
              </svg>
            </div>
            <div className="text-lg font-semibold font-mono text-fg">{fmt(m.value)}</div>
            {METRIC_TOOLTIPS[m.label] && (
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-2 text-xs text-fg bg-bg-elevated border border-line rounded-lg shadow-xl z-50 w-64 text-left opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                {METRIC_TOOLTIPS[m.label]}
                <div className="absolute top-full left-1/2 -translate-x-1/2 w-0 h-0 border-l-4 border-r-4 border-t-4 border-l-transparent border-r-transparent border-t-line" />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
