'use client'

import { useEffect, useState } from 'react'
import { fetchAPI } from '@/lib/api'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { appliedEssenceSummary } from '@/lib/applied-essence'
import type { ContentPhilosophy } from '@/types'

/**
 * 현재 적용 중인 운영 기준 — 읽기 전용.
 *
 * 운영 기준은 승인 단계 없이 자동으로 만들어 바로 적용된다(Phase 4 D33). 편집·재검수·승인
 * 버튼은 없고, 판 번호·적용 시각·입력 자료 수와 자동 정리 결과(뺀 문장·정리한 충돌)만 보여 준다.
 */
export function AppliedEssencePanel({ hospitalId }: { hospitalId: string }) {
  const [approved, setApproved] = useState<ContentPhilosophy | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')

  useEffect(() => {
    let cancelled = false
    fetchAPI<{ approved: ContentPhilosophy | null }>(
      `/admin/hospitals/${hospitalId}/essence/philosophy/approved`,
    )
      .then((response) => {
        if (cancelled) return
        setApproved(response.approved)
        setState('ready')
      })
      .catch(() => {
        if (!cancelled) setState('failed')
      })
    return () => {
      cancelled = true
    }
  }, [hospitalId])

  const summary = approved ? appliedEssenceSummary(approved) : null

  return (
    <section
      aria-label={ADMIN_COPY.appliedEssenceTitle}
      className="space-y-3 rounded-xl border border-slate-200 bg-white p-4"
    >
      <h2 className="text-sm font-semibold text-slate-900">{ADMIN_COPY.appliedEssenceTitle}</h2>
      <p className="text-xs text-slate-500">{ADMIN_COPY.appliedEssenceHint}</p>
      {state === 'loading' && <p className="text-sm text-slate-500">불러오는 중...</p>}
      {state === 'failed' && (
        <p className="text-sm text-slate-500">{ADMIN_COPY.appliedEssenceLoadFailed}</p>
      )}
      {state === 'ready' && !summary && (
        <p className="text-sm text-slate-500">{ADMIN_COPY.appliedEssenceEmpty}</p>
      )}
      {summary && (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
          {summary.map((row) => (
            <div key={row.label}>
              <dt className="text-xs text-slate-500">{row.label}</dt>
              <dd className="font-medium text-slate-900">{row.value}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  )
}
