"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { cn } from "@/lib/utils"

const navLinks = [
  {
    label: "Runs",
    href: "/runs",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <line x1="2" y1="4" x2="14" y2="4" />
        <line x1="2" y1="8" x2="14" y2="8" />
        <line x1="2" y1="12" x2="14" y2="12" />
      </svg>
    ),
  },
  {
    label: "Comparisons",
    href: "/compare",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <rect x="1" y="8" width="3" height="6" />
        <rect x="6.5" y="4" width="3" height="10" />
        <rect x="12" y="1" width="3" height="13" />
      </svg>
    ),
  },
  {
    label: "Leaderboard",
    href: "/leaderboard",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M8 1l2 4 4.5.7-3.3 3.1.8 4.5L8 11.2 3.9 13.3l.8-4.5L1.5 5.7 6 5z" />
      </svg>
    ),
  },
]

export default function Sidebar() {
  const pathname = usePathname()

  return (
    <aside className="fixed left-0 top-0 w-56 h-screen bg-bg-surface border-r border-line flex flex-col z-40">
      {/* Logo */}
      <div className="px-4 py-5 border-b border-line">
        <Link href="/runs" className="flex items-center gap-2.5">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
            <path d="M12 2L2 7l10 5 10-5-10-5z" fill="#F97316" opacity="0.8" />
            <path d="M2 17l10 5 10-5" stroke="#F97316" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M2 12l10 5 10-5" stroke="#F97316" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="font-display text-lg font-bold tracking-tight text-fg">
            eval harness
          </span>
        </Link>
      </div>

      {/* Action buttons */}
      <div className="px-3 py-4 flex flex-col gap-2 border-b border-line">
        <Link href="/runs/new" className="btn btn-primary justify-center text-sm font-semibold">
          + New Run
        </Link>
        <Link href="/ingest" className="btn btn-secondary justify-center text-sm">
          Ingest Data
        </Link>
        <Link href="/compare/new" className="btn btn-secondary justify-center text-sm">
          Compare
        </Link>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-2 py-3 flex flex-col gap-1">
        {navLinks.map((link) => {
          const isActive =
            pathname === link.href || pathname.startsWith(link.href + "/")
          return (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                "flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors",
                isActive
                  ? "bg-accent/10 text-accent"
                  : "text-fg-secondary hover:text-fg hover:bg-bg-elevated"
              )}
            >
              {link.icon}
              {link.label}
            </Link>
          )
        })}
      </nav>
    </aside>
  )
}
