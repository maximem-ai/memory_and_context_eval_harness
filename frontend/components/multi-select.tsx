"use client"
import { useState, useRef, useEffect } from "react"

interface MultiSelectProps {
  label: string
  options: { value: string; label: string; count?: number }[]
  selected: string[]
  onChange: (selected: string[]) => void
}

export default function MultiSelect({ label, options, selected, onChange }: MultiSelectProps) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [open])

  const toggle = (value: string) => {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value])
  }

  const display = selected.length === 0 ? label : selected.length === 1 ? `${selected[0]}` : `${selected.length} selected`

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`select text-left w-full flex items-center justify-between gap-2 ${selected.length > 0 ? "text-fg border-accent/50" : "text-fg-muted"}`}
      >
        <span className="truncate text-sm">{display}</span>
        <svg className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 10 6"><path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.5" /></svg>
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-56 bg-bg-elevated border border-line rounded-lg shadow-xl z-50 max-h-64 overflow-y-auto py-1">
          {options.map((opt) => (
            <button
              key={opt.value}
              onClick={() => toggle(opt.value)}
              className="w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-bg-surface text-left"
            >
              <span className={`w-4 h-4 rounded border flex items-center justify-center shrink-0 ${selected.includes(opt.value) ? "bg-accent border-accent" : "border-line"}`}>
                {selected.includes(opt.value) && <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 10 8"><path d="M1 4l3 3 5-6" stroke="currentColor" strokeWidth="1.5" /></svg>}
              </span>
              <span className="text-fg truncate">{opt.label}</span>
              {opt.count != null && <span className="ml-auto text-xs text-fg-muted">{opt.count}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
