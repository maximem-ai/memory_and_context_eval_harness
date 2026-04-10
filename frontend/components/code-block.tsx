"use client"
import { useState } from "react"
import { Highlight, themes } from "prism-react-renderer"
import CopyButton from "./copy-button"

interface CodeBlockProps {
  code: string
  language?: string
  label?: string
  maxHeight?: string
  collapsible?: boolean
  defaultCollapsed?: boolean
}

export default function CodeBlock({ code, language = "json", label, maxHeight = "300px", collapsible = false, defaultCollapsed = false }: CodeBlockProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed)

  return (
    <div className="rounded-lg border border-line overflow-hidden">
      {label && (
        <div className="flex items-center justify-between px-3 py-2 bg-bg-elevated border-b border-line">
          <button
            onClick={collapsible ? () => setCollapsed(!collapsed) : undefined}
            className={`text-xs text-fg-muted font-medium flex items-center gap-1.5 ${collapsible ? "cursor-pointer hover:text-fg" : ""}`}
          >
            {collapsible && (
              <svg className={`w-3 h-3 transition-transform ${collapsed ? "" : "rotate-90"}`} fill="none" viewBox="0 0 16 16" stroke="currentColor" strokeWidth="2">
                <path d="M6 4l4 4-4 4" strokeLinecap="round" />
              </svg>
            )}
            {label}
          </button>
          <CopyButton text={code} />
        </div>
      )}
      {!collapsed && (
        <div className="overflow-auto" style={{ maxHeight }}>
          <Highlight theme={themes.oneDark} code={code} language={language}>
            {({ style, tokens, getLineProps, getTokenProps }) => (
              <pre className="p-3 text-[11px] leading-relaxed font-mono m-0" style={{ ...style, background: "#0a0a0a" }}>
                {tokens.map((line, i) => (
                  <div key={i} {...getLineProps({ line })}>
                    <span className="inline-block w-8 text-right mr-3 text-fg-muted/30 select-none">{i + 1}</span>
                    {line.map((token, key) => (
                      <span key={key} {...getTokenProps({ token })} />
                    ))}
                  </div>
                ))}
              </pre>
            )}
          </Highlight>
        </div>
      )}
    </div>
  )
}
