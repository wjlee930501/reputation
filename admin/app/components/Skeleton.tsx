export function SkeletonRow({ className = '' }: { className?: string }) {
  return (
    <div className={`animate-pulse rounded-lg bg-slate-200 ${className}`} />
  )
}

export function SkeletonTable({ rows = 5 }: { rows?: number }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <div className="border-b border-slate-100 px-6 py-4">
        <SkeletonRow className="h-5 w-32" />
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-4 border-b border-slate-50 px-6 py-3.5 last:border-b-0"
        >
          <SkeletonRow className="h-4 w-3/5" />
          <SkeletonRow className="h-4 w-16" />
          <SkeletonRow className="h-5 w-20 rounded-full" />
        </div>
      ))}
    </div>
  )
}

export function SkeletonCard({ className = '' }: { className?: string }) {
  return (
    <div className={`rounded-xl border border-slate-200 bg-white p-6 ${className}`}>
      <SkeletonRow className="mb-3 h-5 w-1/3" />
      <SkeletonRow className="mb-2 h-4 w-full" />
      <SkeletonRow className="h-4 w-2/3" />
    </div>
  )
}

export function SkeletonPage({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-4 p-4 sm:p-6 lg:p-8">
      <SkeletonRow className="mb-6 h-8 w-48" />
      <SkeletonTable rows={rows} />
    </div>
  )
}
