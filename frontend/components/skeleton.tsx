export function Skeleton({ className }: { className?: string }) {
  return (
    <div className={`skeleton-shimmer rounded ${className || ""}`} />
  )
}

export function PageSkeleton() {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="flex flex-col items-center gap-3">
        <div className="w-6 h-6 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
        <span className="text-xs text-fg-muted">Loading...</span>
      </div>
    </div>
  )
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="flex items-center justify-center h-64">
      <div className="flex flex-col items-center gap-3">
        <div className="w-6 h-6 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
        <span className="text-xs text-fg-muted">Loading runs...</span>
      </div>
    </div>
  )
}
