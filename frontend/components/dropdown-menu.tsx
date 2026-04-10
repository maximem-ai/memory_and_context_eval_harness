"use client"
import { useState, useRef, useEffect } from "react"

interface MenuItem {
  label: string; onClick: () => void
  danger?: boolean; disabled?: boolean
}

export default function DropdownMenu({ items }: { items: MenuItem[] }) {
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

  return (
    <div ref={ref} className="relative">
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(!open) }}
        className="p-1.5 rounded hover:bg-bg-elevated text-fg-muted hover:text-fg transition-colors"
      >
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 16 16">
          <circle cx="8" cy="3" r="1.5" /><circle cx="8" cy="8" r="1.5" /><circle cx="8" cy="13" r="1.5" />
        </svg>
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 w-48 bg-bg-elevated border border-line rounded-lg shadow-xl z-50 py-1">
          {items.map((item, i) => (
            <button
              key={i}
              onClick={(e) => { e.stopPropagation(); item.onClick(); setOpen(false) }}
              disabled={item.disabled}
              className={`w-full text-left px-3 py-2 text-sm transition-colors ${
                item.disabled ? "opacity-40 cursor-not-allowed" :
                item.danger ? "text-red-400 hover:bg-red-600/10" :
                "text-fg hover:bg-bg-surface"
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
