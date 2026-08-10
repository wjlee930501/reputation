'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchAPI } from '@/lib/api'
import { parseMonthValue, previousMonthValue } from '@/lib/report-period'
import {
  getOrCreateReportRequestKey,
  parseReportRuns,
  reportRebuildFingerprint,
  reportRebuildIdempotencyKey,
  reportRunDeveloperNote,
  type ReportRunView,
} from '@/lib/report-run'
import { ReportRunCard } from './ReportRunCard'

export function ReportRunStatus({ hospitalId, onReview }: { hospitalId: string; onReview: (reportId: string) => void }) {
  const [runs, setRuns] = useState<readonly ReportRunView[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [period, setPeriod] = useState(previousMonthValue())
  const [busy, setBusy] = useState(false)
  const active = useRef(false)
  const keys = useRef(new Map<string, string>())

  const refresh = useCallback(async () => {
    try {
      const payload = await fetchAPI<unknown>(`/admin/hospitals/${hospitalId}/operations/monthly-report-runs`)
      setRuns(parseReportRuns(payload))
      setError(null)
    } catch {
      setError('작업 기록을 불러오지 못했습니다. 다시 시도하고, 계속 실패하면 개발팀에 병원명을 알려 주세요.')
    } finally {
      setLoading(false)
    }
  }, [hospitalId])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!runs.some((run) => run.isActive)) return
    const timer = window.setInterval(() => void refresh(), 5000)
    return () => window.clearInterval(timer)
  }, [refresh, runs])

  async function generate(run?: ReportRunView, reason = '') {
    if (active.current) return
    const parsed = run ? { year: run.periodYear, month: run.periodMonth } : parseMonthValue(period)
    if (!parsed) { setError('생성할 월을 선택해 주세요.'); return }
    active.current = true
    setBusy(true)
    setError(null)
    try {
      const fingerprint = run
        ? reportRebuildFingerprint(run.runId, parsed.year, parsed.month, reason)
        : `create\u0000${hospitalId}\u0000${parsed.year}\u0000${parsed.month}`
      const key = getOrCreateReportRequestKey(keys.current, fingerprint, () => run
        ? reportRebuildIdempotencyKey(run.runId, crypto.randomUUID())
        : `monthly-report-create:${hospitalId}:${crypto.randomUUID()}`)
      await fetchAPI(`/admin/hospitals/${hospitalId}/operations/generate-monthly-report?year=${parsed.year}&month=${parsed.month}${run ? '&rebuild=true' : ''}`, {
        method: 'POST', headers: { 'Idempotency-Key': key }, body: JSON.stringify(run ? { reason } : {}),
      })
      keys.current.delete(fingerprint)
      setMessage(run ? '새 버전 생성을 요청했습니다. 기존 리포트는 보존됩니다.' : '리포트 생성을 요청했습니다. 최근 작업에서 진행 상태를 확인하세요.')
      await refresh()
    } catch {
      setError('문제: 리포트 생성 요청을 완료하지 못했습니다. 고객 영향: 새 원장 보고 자료가 만들어지지 않았습니다. 지금 할 일: 다시 시도하고, 계속 실패하면 개발팀 문의용 정보를 복사해 전달해 주세요.')
    } finally { active.current = false; setBusy(false) }
  }

  async function copy(run: ReportRunView) {
    try { await navigator.clipboard.writeText(reportRunDeveloperNote(hospitalId, run)); setMessage('개발팀 문의용 정보를 복사했습니다.') }
    catch { setMessage('복사하지 못했습니다. 개발팀에 병원명과 대상 월을 알려 주세요.') }
  }

  async function copyPageError() {
    try {
      await navigator.clipboard.writeText(['월간 리포트 작업 확인 요청', `병원 ID: ${hospitalId}`, `대상 월: ${period}`, `확인 시각: ${new Date().toISOString()}`].join('\n'))
      setMessage('개발팀 문의용 정보를 복사했습니다.')
    } catch { setMessage('복사하지 못했습니다. 개발팀에 병원명과 대상 월을 알려 주세요.') }
  }

  return (
    <section className="mb-6 rounded-xl border border-[var(--color-revisit-coolgrey-20)] bg-white p-4 sm:p-5" aria-labelledby="run-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><h3 id="run-heading" className="font-bold text-[var(--color-revisit-text-title)]">최근 월간 리포트 작업</h3><p className="mt-1 text-sm text-[var(--color-revisit-text-helper)]">문제와 고객 영향을 확인하고 필요한 조치를 바로 실행합니다.</p></div>
        <button type="button" onClick={() => void refresh()} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">진행 상태 새로고침</button>
      </div>
      {loading && <p className="mt-4 text-sm" role="status">작업 기록을 불러오는 중입니다.</p>}
      {error && <div className="mt-4 rounded-lg border border-[var(--color-revisit-red-50)] p-3 text-sm text-[var(--color-revisit-red-50)]" role="alert"><p>{error}</p><div className="mt-3 flex flex-col gap-2 sm:flex-row"><button type="button" onClick={() => void refresh()} className="min-h-11 rounded-lg bg-[var(--color-revisit-primary-40)] px-4 font-bold text-white">작업 기록 다시 시도</button><button type="button" onClick={() => void copyPageError()} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 font-bold text-[var(--color-revisit-text-title)]">개발팀 문의용 정보 복사</button></div></div>}
      <div className="mt-4 grid gap-3">{runs.slice(0, 3).map((run) => <ReportRunCard key={run.runId} run={run} disabled={busy} operationsHref={`/operations?queue=REPORTS&hospital_id=${hospitalId}`} onReview={() => run.reportId ? onReview(run.reportId) : setError('연결된 리포트를 찾지 못했습니다. 새로고침 후 계속 보이지 않으면 개발팀에 문의해 주세요.')} onRebuild={(reason) => void generate(run, reason)} onCopy={() => void copy(run)} />)}</div>
      {message && <p className="mt-3 text-sm text-[var(--color-revisit-text-helper)]" role="status">{message}</p>}
      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-[var(--color-revisit-coolgrey-20)] pt-4">
        <label htmlFor="report-period" className="text-sm font-bold">리포트가 없다면 대상 월 선택</label>
        <input id="report-period" type="month" value={period} max={previousMonthValue()} onChange={(event) => setPeriod(event.target.value)} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-3 text-sm" />
        <button type="button" onClick={() => void generate()} disabled={busy} className="min-h-11 rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white disabled:opacity-50">{busy ? '요청 중' : '리포트 생성'}</button>
      </div>
    </section>
  )
}
