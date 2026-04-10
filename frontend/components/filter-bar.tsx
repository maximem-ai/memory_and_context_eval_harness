"use client"
import MultiSelect from "./multi-select"

interface FilterOption { value: string; label: string; count?: number }
interface FilterConfig {
  key: string; label: string; options: FilterOption[]
  selected: string[]; onChange: (selected: string[]) => void
}
interface FilterBarProps {
  totalCount: number
  filteredCount?: number
  searchValue: string
  onSearchChange: (v: string) => void
  searchPlaceholder?: string
  filters: FilterConfig[]
  onClearAll: () => void
}

export default function FilterBar({ totalCount, filteredCount, searchValue, onSearchChange, searchPlaceholder, filters, onClearAll }: FilterBarProps) {
  const hasFilters = searchValue || filters.some((f) => f.selected.length > 0)
  const count = filteredCount ?? totalCount

  return (
    <div className="bg-bg-surface border border-line rounded-t-lg">
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-line/50">
        <span className="text-xs text-fg-muted">Showing {count} {count === 1 ? "entry" : "entries"}</span>
        {hasFilters && (
          <button onClick={onClearAll} className="text-xs text-accent hover:text-accent-hover">Clear filters</button>
        )}
      </div>
      <div className="flex items-center gap-2 px-4 py-2.5">
        <div className="relative">
          <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-fg-muted" fill="none" viewBox="0 0 16 16">
            <circle cx="7" cy="7" r="5.5" stroke="currentColor" strokeWidth="1.5" />
            <path d="M11 11l3.5 3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <input
            type="text"
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
            placeholder={searchPlaceholder || "Search..."}
            className="input pl-8 w-60 text-sm"
          />
        </div>
        {filters.map((f) => (
          <MultiSelect key={f.key} label={f.label} options={f.options} selected={f.selected} onChange={f.onChange} />
        ))}
      </div>
    </div>
  )
}
