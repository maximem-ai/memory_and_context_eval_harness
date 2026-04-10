"use client"

interface PhaseProgressProps {
  summary: { total: number; searched: number; answered: number; evaluated: number }
}

function Bar({ label, count, total, color }: { label: string; count: number; total: number; color: string }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-fg-secondary">{label}</span>
        <span className="text-xs font-mono text-fg-muted">{count}/{total}</span>
      </div>
      <div className="w-full h-2 bg-bg-elevated rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${color}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export default function PhaseProgress({ summary }: PhaseProgressProps) {
  return (
    <div className="card space-y-3">
      <h3 className="text-sm font-medium text-fg-secondary">Pipeline Progress</h3>
      <Bar label="Search" count={summary.searched} total={summary.total} color="bg-blue-500" />
      <Bar label="Answer" count={summary.answered} total={summary.total} color="bg-purple-500" />
      <Bar label="Evaluate" count={summary.evaluated} total={summary.total} color="bg-emerald-500" />
    </div>
  )
}
