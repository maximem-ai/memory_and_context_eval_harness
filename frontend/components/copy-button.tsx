"use client"
import { useState } from "react"

export default function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }

  return (
    <button onClick={handleCopy} className={`inline-flex items-center gap-1 text-fg-muted hover:text-fg transition-colors ${className || ""}`} title="Copy to clipboard">
      {copied ? (
        <svg className="w-3.5 h-3.5 text-success" fill="none" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="2">
          <path d="M3 8.5l3 3 7-7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      ) : (
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="1.5">
          <rect x="5" y="5" width="8" height="8" rx="1.5" />
          <path d="M3 11V3.5A1.5 1.5 0 014.5 2H11" strokeLinecap="round" />
        </svg>
      )}
    </button>
  )
}
