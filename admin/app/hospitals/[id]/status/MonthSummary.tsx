'use client'

import Link from 'next/link'
import { monthSummaryLines } from '@/lib/status-screen'
import type { HospitalOverviewMonth } from '@/types'

export function MonthSummary({
  hospitalId,
  month,
}: {
  hospitalId: string
  month: HospitalOverviewMonth
}) {
  const lines = monthSummaryLines(month)

  return (
    <section aria-label="이번 달 요약" className="rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-900">
          {month.year}년 {month.month}월
        </h2>
        <div className="flex flex-wrap gap-3 text-xs">
          <Link
            href={`/hospitals/${hospitalId}/content`}
            className="text-blue-700 underline underline-offset-2 hover:text-blue-800"
          >
            발행한 글 보기
          </Link>
          <Link
            href={`/hospitals/${hospitalId}/reports`}
            className="text-blue-700 underline underline-offset-2 hover:text-blue-800"
          >
            보고서 보기
          </Link>
        </div>
      </div>
      <ul className="mt-2 space-y-1">
        {lines.map((line) => (
          <li key={line} className="text-sm text-slate-700">
            {line}
          </li>
        ))}
      </ul>
    </section>
  )
}
