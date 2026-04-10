interface EmptyStateProps {
  title: string
  description?: string
  action?: React.ReactNode
  children?: React.ReactNode
}

export default function EmptyState({ title, description, action, children }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4">
      <h3 className="text-lg font-medium text-fg">{title}</h3>
      {description && <p className="mt-1 text-sm text-fg-secondary">{description}</p>}
      {(action || children) && <div className="mt-4">{action || children}</div>}
    </div>
  )
}
