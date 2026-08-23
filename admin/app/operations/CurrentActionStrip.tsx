'use client'

import Link from 'next/link'

import { describeOperationsDeadline, operationsRowTitle } from '@/lib/operations-center'
import type { OperationsQueueRow } from '@/types'

function formatDeadlineDate(value: string): string {
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value))
}

type Props = {
  readonly item: OperationsQueueRow | null
  readonly onOpen: (item: OperationsQueueRow) => void
  /** 남은 처리 기한 계산의 기준 시각 — 목록을 받은 시점 */
  readonly checkedAt: number
}

export function CurrentActionStrip({ item, onOpen, checkedAt }: Props) {
  if (!item) {
    return (
      <section data-current-task className="ops-current ops-current--empty" aria-labelledby="ops-current-title">
        <div>
          <p className="text-xs font-bold text-emerald-700">지금 먼저 처리</p>
          <h2 id="ops-current-title" className="mt-1 text-base font-bold text-slate-900">긴급한 운영 작업이 없습니다.</h2>
        </div>
        <p className="text-sm text-slate-500">각 탭에서 예정된 업무를 확인할 수 있습니다.</p>
      </section>
    )
  }

  const opensDetail = item.incident_id !== null || item.action.method === 'POST'
  const deadline = describeOperationsDeadline(item, checkedAt, formatDeadlineDate)
  return (
    <section data-current-task className="ops-current" aria-labelledby="ops-current-title">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-xs font-bold text-blue-700">지금 먼저 처리</p>
          {/* 'DUE'는 임박이 아니라 '아직 지나지 않았다'는 뜻이다 — 남은 시간을 실제로 센다(G-2). */}
          {deadline.tone !== 'none' ? (
            <span className={`ops-badge ${deadline.tone === 'overdue' ? 'ops-badge--danger' : 'ops-badge--neutral'}`}>
              {deadline.text}
            </span>
          ) : null}
        </div>
        <h2 id="ops-current-title" title={operationsRowTitle(item)} className="ops-current-title ops-readable mt-1 text-base font-bold text-slate-950">{operationsRowTitle(item)}</h2>
        <p className="ops-current-copy mt-0.5 line-clamp-2 text-sm leading-5 text-slate-600">{item.next_action}</p>
      </div>
      {opensDetail ? (
        <button id="ops-current-action" type="button" onClick={() => onOpen(item)} className="ops-control ops-primary-action rounded-lg px-4 text-sm font-bold text-white">
          원인과 처리 방법 보기
        </button>
      ) : (
        <Link id="ops-current-action" href={item.action.path} className="ops-control ops-primary-action inline-flex items-center justify-center rounded-lg px-4 text-sm font-bold text-white">
          {item.action.label}
        </Link>
      )}
    </section>
  )
}
