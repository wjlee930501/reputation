'use client'

import Link from 'next/link'
import { domainLastCheckedLabel } from '@/lib/hospital-domain-status'
import { stateTone, type StateKind, type StateTone } from '@/lib/hospital-states'
import { splitRemaining, stateCardCopy } from '@/lib/status-screen'
import type { HospitalOverview } from '@/types'
import type { RemainingCondition } from '@/lib/hospital-states'

const TONE_CLASS: Record<StateTone, string> = {
  good: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  warn: 'bg-amber-50 text-amber-800 border-amber-200',
  paused: 'bg-slate-100 text-slate-600 border-slate-200',
  neutral: 'bg-blue-50 text-blue-700 border-blue-200',
}

export function StatusCards({ overview }: { overview: HospitalOverview }) {
  const domainDetail = [
    overview.domain.reason,
    domainLastCheckedLabel(overview.domain.last_checked_at, overview.domain.last_check_ok),
  ].filter((line): line is string => Boolean(line))

  return (
    <section aria-label="병원 상태" className="grid gap-4 md:grid-cols-3">
      <StatusCard title="공개 서비스" card={overview.public_service} />
      <StatusCard title="콘텐츠 준비" card={overview.content} />
      <StatusCard title="자기 도메인" card={overview.domain} detail={domainDetail} />
    </section>
  )
}

function StatusCard({
  title,
  card,
  detail = [],
}: {
  title: string
  card: { kind: StateKind; label: string; remaining: RemainingCondition[] }
  detail?: string[]
}) {
  const copy = stateCardCopy(card)
  const { human, system } = splitRemaining(card.remaining)

  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4">
      <p className="text-xs font-semibold tracking-wide text-slate-500">{title}</p>
      <p
        className={`mt-2 inline-flex rounded-full border px-2.5 py-0.5 text-sm font-semibold ${TONE_CLASS[stateTone(card.kind)]}`}
      >
        {copy.label}
      </p>
      <p className="mt-2 text-xs text-slate-500">{copy.summary}</p>
      {detail.map((line) => (
        <p key={line} className="mt-1 text-xs text-slate-500">
          {line}
        </p>
      ))}
      {human.length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-semibold text-slate-600">할 일</p>
          <ul className="mt-1 space-y-1">
            {human.map((condition) => (
              <li key={condition.key} className="text-sm">
                {condition.href ? (
                  <Link href={condition.href} className="text-blue-700 underline underline-offset-2 hover:text-blue-800">
                    {condition.label}
                  </Link>
                ) : (
                  <span className="text-slate-800">{condition.label}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {system.length > 0 && (
        <div className="mt-3">
          {/* 자동으로 진행 중인 일은 링크를 주지 않는다 — 운영자가 손댈 자리가 없다. */}
          <p className="text-[11px] font-semibold text-slate-600">시스템 처리 중</p>
          <ul className="mt-1 space-y-1">
            {system.map((condition) => (
              <li key={condition.key} className="text-sm text-slate-500">
                {condition.label}
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  )
}
