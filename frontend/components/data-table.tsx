"use client"

import React from "react"

interface Column<T> {
  key: string
  header: string
  render: (item: T, index: number) => React.ReactNode
  align?: "left" | "center" | "right"
  width?: string
}

interface DataTableProps<T> {
  columns: Column<T>[]
  data: T[]
  onRowClick?: (item: T) => void
  emptyMessage?: string
  loading?: boolean
  getRowKey?: (item: T, index: number) => string | number
}

export function DataTable<T>({
  columns,
  data,
  onRowClick,
  emptyMessage = "No data",
  loading = false,
  getRowKey,
}: DataTableProps<T>) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-line border-t-accent" />
      </div>
    )
  }

  const alignClass = (align?: "left" | "center" | "right") => {
    if (align === "center") return "text-center"
    if (align === "right") return "text-right"
    return "text-left"
  }

  return (
    <div className="w-full overflow-x-auto">
      <table className="w-full border-collapse">
        <thead>
          <tr className="bg-bg-surface sticky top-0 z-10">
            {columns.map((col) => (
              <th
                key={col.key}
                className={`table-header ${alignClass(col.align)}`}
                style={col.width ? { width: col.width } : undefined}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.length === 0 ? (
            <tr>
              <td
                colSpan={columns.length}
                className="table-cell text-center text-fg-muted py-12"
              >
                {emptyMessage}
              </td>
            </tr>
          ) : (
            data.map((item, index) => (
              <tr
                key={getRowKey ? getRowKey(item, index) : index}
                onClick={onRowClick ? () => onRowClick(item) : undefined}
                className={`border-b border-line/50 ${
                  onRowClick ? "hover:bg-bg-elevated/50 cursor-pointer" : ""
                }`}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={`table-cell ${alignClass(col.align)}`}
                    style={col.width ? { width: col.width } : undefined}
                  >
                    {col.render(item, index)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
