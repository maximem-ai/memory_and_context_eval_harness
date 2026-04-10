import type { Metadata } from "next"
import { GeistSans } from "geist/font/sans"
import { GeistMono } from "geist/font/mono"
import "./globals.css"
import Sidebar from "@/components/sidebar"

export const metadata: Metadata = {
  title: "Eval Harness",
  description: "Memory system evaluation platform",
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={`${GeistSans.variable} ${GeistMono.variable}`}>
      <body className="flex font-body">
        <Sidebar />
        <main className="ml-56 flex-1 min-h-screen p-8">{children}</main>
      </body>
    </html>
  )
}
