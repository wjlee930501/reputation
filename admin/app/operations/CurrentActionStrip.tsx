'use client'

import Link from 'next/link'

import type { OperationsQueueRow } from '@/types'

type Props = {
  readonly item: OperationsQueueRow | null
  readonly onOpen: (item: OperationsQueueRow) => void
}

export function CurrentActionStrip({ item, onOpen }: Props) {
  if (!item) {
    return (
      <section className="ops-current ops-current--empty" aria-labelledby="ops-current-title">
        <div>
          <p className="text-xs font-bold text-emerald-700">지금 먼저 처리</p>
          <h2 id="ops-current-title" className="mt-1 text-base font-bold text-slate-900">긴급한 운영 작업이 없습니다.</h2>
        </div>
        <p className="text-sm text-slate-500">각 탭에서 예정된 업무를 확인할 수 있습니다.</p>
      </section>
    )
  }

  const opensDetail = item.incident_id !== null || item.action.method === 'POST'
  return (
    <section className="ops-current" aria-labelledby="ops-current-title">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-xs font-bold text-blue-700">지금 먼저 처리</p>
          {item.sla_state === 'OVERDUE' ? <span className="ops-badge ops-badge--danger">처리 기한 지남</span> : null}
          {item.sla_state === 'DUE' ? <span className="ops-badge ops-badge--neutral">처리 기한 임박</span> : null}
        </div>
        <h2 id="ops-current-title" title={item.customer.name} className="ops-current-title ops-readable mt-1 text-base font-bold text-slate-950">{item.customer.name}</h2>
        <p className="ops-current-copy mt-0.5 line-clamp-2 text-sm leading-5 text-slate-600">{item.next_action}</p>
      </div>
      {opensDetail ? (
        <button id="ops-current-action" type="button" onClick={() => onOpen(item)} className="ops-control ops-primary-action rounded-lg px-4 text-sm font-bold text-white">
          안전하게 처리하기
        </button>
      ) : (
        <Link id="ops-current-action" href={item.action.path} className="ops-control ops-primary-action inline-flex items-center justify-center rounded-lg px-4 text-sm font-bold text-white">
          {item.action.label}
        </Link>
      )}
    </section>
  )
}
