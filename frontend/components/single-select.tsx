"use client"
import { useState, useRef, useEffect } from "react"

interface SingleSelectProps {
  label: string
  options: { value: string; label: string; sublabel?: string }[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
}

export default function SingleSelect({ label, options, value, onChange, placeholder }: SingleSelectProps) {
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

  const selected = options.find((o) => o.value === value)

  return (
    <div ref={ref} className="relative">
      <label className="block text-xs text-fg-muted mb-1.5 font-medium">{label}</label>
      <button
        onClick={() => setOpen(!open)}
        className={`select text-left w-full flex items-center justify-between gap-2 ${value ? "text-fg" : "text-fg-muted"}`}
      >
        <span className="truncate text-sm">{selected?.label || placeholder || "Select..."}</span>
        <svg className="w-3 h-3 shrink-0" fill="none" viewBox="0 0 10 6"><path d="M1 1l4 4 4-4" stroke="currentColor" strokeWidth="1.5" /></svg>
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 w-full min-w-[200px] bg-bg-elevated border border-line rounded-lg shadow-xl z-50 max-h-64 overflow-y-auto py-1">
          {options.map((opt) => (
            <button
              key={opt.value}
              onClick={() => { onChange(opt.value); setOpen(false) }}
              className={`w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-bg-surface text-left ${opt.value === value ? "text-accent" : "text-fg"}`}
            >
              <span className={`w-3 h-3 rounded-full border shrink-0 ${opt.value === value ? "bg-accent border-accent" : "border-line"}`} />
              <div>
                <div>{opt.label}</div>
                {opt.sublabel && <div className="text-xs text-fg-muted">{opt.sublabel}</div>}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
