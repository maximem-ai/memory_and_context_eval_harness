"use client"

const COLORS = ["#4A9EF5", "#F5845A", "#F5D45A", "#5AF5A0", "#A05AF5", "#5AE0F5"]

interface AccuracyBarChartProps {
  data: { type: string; values: { provider: string; accuracy: number | undefined }[] }[]
  providers: string[]
}

export default function AccuracyBarChart({ data, providers }: AccuracyBarChartProps) {
  if (!data.length) return null

  const W = 800, H = 320, PAD = { top: 30, right: 20, bottom: 60, left: 50 }
  const chartW = W - PAD.left - PAD.right
  const chartH = H - PAD.top - PAD.bottom
  const groupW = chartW / data.length
  const barW = Math.min(32, (groupW - 8) / providers.length)

  return (
    <div>
      <h3 className="text-sm font-medium text-fg-secondary mb-3">Accuracy Comparison</h3>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full max-w-4xl">
          {/* Grid lines */}
          {[0, 25, 50, 75, 100].map((v) => {
            const y = PAD.top + chartH - (v / 100) * chartH
            return (
              <g key={v}>
                <line x1={PAD.left} y1={y} x2={W - PAD.right} y2={y} stroke="#1e2530" strokeDasharray="4,4" />
                <text x={PAD.left - 8} y={y + 4} textAnchor="end" className="fill-muted" fontSize="10">{v}%</text>
              </g>
            )
          })}

          {/* Bars */}
          {data.map((group, gi) => {
            const gx = PAD.left + gi * groupW + groupW / 2
            return (
              <g key={group.type}>
                {group.values.map((v, vi) => {
                  const acc = v.accuracy ?? 0
                  const barH = (acc / 100) * chartH
                  const x = gx - (providers.length * barW) / 2 + vi * barW
                  const y = PAD.top + chartH - barH
                  const color = COLORS[vi % COLORS.length]
                  return (
                    <g key={v.provider}>
                      <rect x={x} y={y} width={barW - 2} height={barH} fill={color} rx={2} ry={2} opacity={0.85} />
                      {acc > 0 && (
                        <text x={x + (barW - 2) / 2} y={y - 4} textAnchor="middle" className="fill-secondary" fontSize="9" fontFamily="monospace">
                          {acc.toFixed(0)}
                        </text>
                      )}
                    </g>
                  )
                })}
                <text x={gx} y={H - PAD.bottom + 16} textAnchor="middle" className="fill-muted" fontSize="10">
                  {group.type.replace(/[-_]/g, " ")}
                </text>
              </g>
            )
          })}

          {/* Legend */}
          {providers.map((p, i) => (
            <g key={p} transform={`translate(${PAD.left + i * 100}, ${8})`}>
              <rect width="10" height="10" rx="2" fill={COLORS[i % COLORS.length]} opacity={0.85} />
              <text x="14" y="9" className="fill-secondary" fontSize="10">{p}</text>
            </g>
          ))}
        </svg>
      </div>
    </div>
  )
}
