'use client'

import { useEffect, useState } from 'react'
import { fetchAPI } from '@/lib/api'
import {
  describeTrackingSet,
  isCurrentKoreanMonth,
  latestMentionLabel,
  thisMonthTargets,
} from '@/lib/content-signals'
import { EXPOSURE_ACTION_LIST_LIMIT } from '@/lib/exposure-action-counts'
import type { AIQueryTarget, ExposureAction } from '@/types'

/** 옛 `/exposure-actions` 경로가 오는 자리. 이 앵커로 오면 접힌 제안을 펼쳐 보인다. */
export const EXPOSURE_ACTIONS_ANCHOR = 'content-exposure-actions'

/**
 * 콘텐츠 화면 하단의 읽기 전용 신호 — 이번 달 환자 질문과 노출 보완 제안을 정보로만 보여 준다.
 * 여기서는 아무것도 만들지 않고 바꾸지 않는다(설계 §4.3).
 *
 * 노출 보완 제안은 실행 버튼이 없는 참고 정보라 기본으로 접는다(2026-11 개편 결정 대기 13번 A —
 * 월간 보고서 「다음 달 할 일」의 근거라 지우지는 않는다). 제목 줄에 건수만 보인다.
 */
export function ReadOnlySignals({
  hospitalId,
  year,
  month,
}: {
  hospitalId: string
  year: number
  month: number
}) {
  const [targets, setTargets] = useState<AIQueryTarget[]>([])
  const [actions, setActions] = useState<ExposureAction[]>([])
  const [loading, setLoading] = useState(true)
  // 한쪽이 실패해도 다른 쪽은 그대로 보여 준다 — 대신 못 받아온 목록을 숨기지 않고 말한다.
  const [failed, setFailed] = useState<string[]>([])
  const [actionsOpen, setActionsOpen] = useState(false)

  // 옛 노출 보완 경로에서 넘어온 사람은 바로 제안을 봐야 한다.
  useEffect(() => {
    if (window.location.hash === `#${EXPOSURE_ACTIONS_ANCHOR}`) setActionsOpen(true)
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void Promise.allSettled([
      fetchAPI<AIQueryTarget[]>(`/admin/hospitals/${hospitalId}/query-targets`),
      fetchAPI<ExposureAction[]>(
        `/admin/hospitals/${hospitalId}/exposure-actions?limit=${EXPOSURE_ACTION_LIST_LIMIT}`,
      ),
    ]).then(([targetResult, actionResult]) => {
      if (cancelled) return
      const missing: string[] = []
      if (targetResult.status === 'fulfilled') setTargets(targetResult.value ?? [])
      else missing.push('환자 질문')
      if (actionResult.status === 'fulfilled') setActions(actionResult.value ?? [])
      else missing.push('노출 보완 제안')
      setFailed(missing)
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [hospitalId])

  const monthTargets = thisMonthTargets(targets, year, month)
  // 지난달을 조회 중인데 "이번 달"이라고 쓰면 화면이 거짓말을 한다.
  const monthLabel = isCurrentKoreanMonth(year, month) ? '이번 달' : `${month}월`

  return (
    <section id="content-signals" className="mt-8 grid scroll-mt-4 items-start gap-4 lg:grid-cols-2">
      {failed.length > 0 && (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 lg:col-span-2">
          {failed.join('·')} 목록을 불러오지 못했습니다. 화면을 다시 불러오면 표시됩니다.
        </p>
      )}

      <div className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
        <h3 className="text-sm font-semibold text-slate-800">{monthLabel} 환자 질문</h3>
        <p className="mt-1 text-xs text-slate-500">
          {monthLabel}에 확인하는 질문입니다. ‘측정 대상’으로 표시된 질문만 실제로 측정합니다.
        </p>
        <ul className="mt-3 divide-y divide-slate-100">
          {!loading && monthTargets.length === 0 && (
            <li className="py-3 text-sm text-slate-400">{monthLabel} 환자 질문이 없습니다.</li>
          )}
          {monthTargets.map((target) => (
            <li key={target.id} className="py-3">
              <p className="text-sm font-medium text-slate-900">{target.name}</p>
              <p className="mt-0.5 text-xs text-slate-500">
                {describeTrackingSet(target)} · {latestMentionLabel(target.summary)}
              </p>
            </li>
          ))}
        </ul>
      </div>

      <details
        id={EXPOSURE_ACTIONS_ANCHOR}
        open={actionsOpen}
        onToggle={(event) => setActionsOpen(event.currentTarget.open)}
        className="scroll-mt-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-5"
      >
        <summary className="flex min-h-11 cursor-pointer items-center justify-between gap-2 text-sm font-semibold text-slate-800">
          <span>노출 보완 제안</span>
          <span className="text-xs font-normal text-slate-500">
            {loading ? '불러오는 중…' : `${actions.length}건 · ${actionsOpen ? '접기' : '펼쳐 보기'}`}
          </span>
        </summary>
        <p className="mt-1 text-xs text-slate-500">
          AI 답변에서 병원이 덜 언급되는 이유에 대한 제안입니다. 정보로만 보여 줍니다.
        </p>
        <ul className="mt-3 divide-y divide-slate-100">
          {!loading && actions.length === 0 && (
            <li className="py-3 text-sm text-slate-400">보완 제안이 없습니다.</li>
          )}
          {actions.map((action) => (
            <li key={action.id} className="py-3">
              <p className="text-sm font-medium text-slate-900">{action.title}</p>
              <p className="mt-0.5 text-xs text-slate-500">
                {action.display?.status_label ?? action.status}
                {action.display?.action_type_label ? ` · ${action.display.action_type_label}` : ''}
                {action.query_target?.name ? ` · ${action.query_target.name}` : ''}
              </p>
              <p className="mt-1 text-xs text-slate-600">
                연결된 콘텐츠: {action.linked_content?.title ?? '연결 없음'}
              </p>
            </li>
          ))}
        </ul>
      </details>
    </section>
  )
}
