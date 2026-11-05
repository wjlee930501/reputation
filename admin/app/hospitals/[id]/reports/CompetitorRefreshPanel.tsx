'use client'

import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, fetchAPI } from '@/lib/api'
import {
  COMPETITOR_REDELIVER_WARNING,
  competitorRefreshIdempotencyKey,
  competitorRefreshIsActive,
  competitorRefreshPlan,
  competitorRefreshProgress,
  parseCompetitorRefreshStatus,
  type CompetitorRefreshStatus,
} from '@/lib/competitor-refresh'
import { getOrCreatePendingActionKey } from '@/lib/pending-action-key'
import type { ReportView } from '@/lib/report-review'

/**
 * 보고서 상세의 ‘경쟁 병원 반영’.
 *
 * 경쟁 병원을 뒤늦게 등록한 달의 보고서에서 경쟁 현황만 다시 만든다 — 저장된 AI 답변에 등록 경쟁 병원의
 * 판정을 보충한 뒤, 측정 숫자는 그대로 두고 경쟁 현황만 다시 계산한 새 버전을 만든다. 등록 자체는 병원
 * 정보 탭에서 한다(여기에는 입력이 없다). 새 버전은 다시 전달해야 원장님께 닿는다.
 */
export function CompetitorRefreshPanel({
  hospitalId,
  report,
  onChanged,
}: {
  hospitalId: string
  report: ReportView
  /** 체인을 요청했을 때와 체인이 끝났을 때 — 작업 기록과 보고서 목록을 다시 읽게 한다(새 버전이 목록에 보이도록). */
  onChanged?: () => void
}) {
  const [status, setStatus] = useState<CompetitorRefreshStatus | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [requestError, setRequestError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const keys = useRef(new Map<string, string>())
  const wasActive = useRef(false)
  const statusPath = `/admin/hospitals/${hospitalId}/reports/competitors/refresh?year=${report.periodYear}&month=${report.periodMonth}`

  const load = useCallback(async () => {
    try {
      const parsed = parseCompetitorRefreshStatus(await fetchAPI<unknown>(statusPath))
      if (!parsed) throw new Error('응답 형식')
      setStatus(parsed)
      setLoadError(null)
    } catch {
      setLoadError('경쟁 병원 반영 상태를 불러오지 못했습니다. ‘상태 다시 확인’을 눌러 주세요.')
    }
  }, [statusPath])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!status) return
    const active = competitorRefreshIsActive(status.latestRun)
    // 진행 중 → 끝남으로 바뀌면 부모에게 알린다 — 새 버전이 목록·작업 기록에 보여야 운영자가 옛 버전을 전달하지 않는다.
    if (wasActive.current && !active) onChanged?.()
    wasActive.current = active
    if (!active) return
    const timer = window.setInterval(() => void load(), 5000)
    return () => window.clearInterval(timer)
  }, [load, onChanged, status])

  async function request() {
    if (busy) return
    setBusy(true)
    setRequestError(null)
    setMessage(null)
    const fingerprint = `${report.id}:${status?.reportVersion ?? 0}`
    try {
      const key = getOrCreatePendingActionKey(
        keys.current,
        fingerprint,
        () => competitorRefreshIdempotencyKey(report.id, crypto.randomUUID()),
      )
      await fetchAPI(statusPath, { method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify({}) })
      keys.current.delete(fingerprint)
      setMessage(`경쟁 병원 반영을 요청했습니다. ${COMPETITOR_REDELIVER_WARNING}`)
      onChanged?.()
      await load()
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        keys.current.delete(fingerprint)
        setRequestError('이 달의 경쟁 병원 반영이 이미 진행 중이거나 조건이 바뀌었습니다. 상태를 다시 확인해 주세요.')
        await load()
      } else {
        setRequestError('문제: 경쟁 병원 반영 요청을 완료하지 못했습니다. 고객 영향: 보고서는 바뀌지 않았습니다. 지금 할 일: 다시 시도하고, 계속 실패하면 개발팀에 병원명과 보고서 기간을 알려 주세요.')
      }
    } finally {
      setBusy(false)
    }
  }

  const plan = status ? competitorRefreshPlan(status) : null
  const progress = status ? competitorRefreshProgress(status.latestRun) : null
  const toneClass =
    progress?.tone === 'problem'
      ? 'border-[var(--color-revisit-red-50)]'
      : progress?.tone === 'done'
        ? 'border-[var(--color-revisit-green-50)]'
        : 'border-[var(--color-revisit-coolgrey-20)]'

  return (
    <section data-report-section="competitors" className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] p-4" aria-labelledby="report-competitors-heading">
      <h4 id="report-competitors-heading" className="text-lg font-bold text-[var(--color-revisit-text-title)]">4. 경쟁 병원 반영</h4>
      <p className="mt-2 text-sm leading-6 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">
        경쟁 병원을 나중에 등록했다면, 이 달의 저장된 AI 답변에서 그 병원의 언급을 확인해 경쟁 현황만 다시 만든 새 버전을 만듭니다. 측정 숫자는 바뀌지 않습니다.
      </p>
      {loadError && (
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center" role="alert">
          <p className="text-sm text-[var(--color-revisit-red-50)]">{loadError}</p>
          <button type="button" onClick={() => void load()} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">상태 다시 확인</button>
        </div>
      )}
      {status && plan && (
        <div className="mt-3 space-y-3">
          <div className="rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-sm leading-6 [word-break:keep-all]">
            <p>
              <strong>등록 경쟁 병원</strong>{' '}
              {status.registeredCompetitors.length > 0 ? status.registeredCompetitors.join(', ') : '없음'}
            </p>
            <p className="mt-1 text-[var(--color-revisit-text-helper)]">{plan.message}</p>
            <p className="mt-1 text-xs text-[var(--color-revisit-text-caption)]">
              등록·수정은 <Link href={`/hospitals/${hospitalId}/info`} className="underline">병원 정보 탭</Link>에서 합니다.
            </p>
          </div>
          {plan.kind === 'ready' && (
            <>
              <p className="text-xs leading-5 text-[var(--color-revisit-red-50)] [word-break:keep-all]" role="note">{COMPETITOR_REDELIVER_WARNING}</p>
              <button
                type="button"
                onClick={() => void request()}
                disabled={busy}
                className="min-h-11 w-full rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white disabled:opacity-40 sm:w-auto"
              >
                {busy ? '요청 중' : '경쟁 병원 반영 — 새 버전 만들기'}
              </button>
            </>
          )}
          {plan.kind === 'register' && (
            <Link href={`/hospitals/${hospitalId}/info`} className="inline-flex min-h-11 items-center justify-center rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">
              병원 정보 탭에서 경쟁 병원 등록
            </Link>
          )}
          {progress && (
            <div className={`rounded-lg border p-3 text-sm leading-6 [word-break:keep-all] ${toneClass}`} role="status">
              <strong>{progress.label}</strong>
              <p className="mt-1 text-[var(--color-revisit-text-helper)]">{progress.detail}</p>
            </div>
          )}
        </div>
      )}
      {message && <p className="mt-3 rounded-lg bg-[var(--color-revisit-primary-95)] p-3 text-sm" role="status">{message}</p>}
      {requestError && <p className="mt-3 text-sm text-[var(--color-revisit-red-50)]" role="alert">{requestError}</p>}
    </section>
  )
}
