'use client'

import Link from 'next/link'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { fetchAPI } from '@/lib/api'
import { ADMIN_COPY } from '@/lib/admin-copy'
import type { AttentionQueue } from '@/lib/attention-queue'
import { summarizeDashboard, todoLines, type TodoTone } from '@/lib/dashboard-summary'
import { metricsByHospital, mentionRateLabel, publishProgress, type HospitalMonthMetrics } from '@/lib/hospital-month-metrics'
import type { NavCounts } from '@/lib/nav-counts'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import { readPublicAddresses } from '@/lib/public-address'
import { AddressLink } from '@/app/_components/AddressLink'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import type { HospitalListRow } from '@/types'

const COPY = ADMIN_COPY.dashboardScreen
const TODO_VISIBLE = 8
const TABLE_VISIBLE = 6

const TONE_BAR: Record<TodoTone, string> = {
  urgent: 'bg-red-500',
  attention: 'bg-amber-500',
  info: 'bg-slate-300',
}

/**
 * 대시보드 — 로그인 직후 첫 화면(2026-11-20 마케팅 요청 화면 1).
 *
 * 새 판정을 만들지 않는다: 병원 목록(3상태·예외 수), 이번 달 지표(현황과 같은 함수),
 * 원장 보고 상자(attention), 메뉴 배지(nav-counts)를 그대로 모아 보여 준다. 자동 작업 한도는 설정에만 둔다.
 */
export default function DashboardPage() {
  const [hospitals, setHospitals] = useState<HospitalListRow[] | null>(null)
  const [metrics, setMetrics] = useState<ReadonlyMap<string, HospitalMonthMetrics>>(new Map())
  const [attention, setAttention] = useState<AttentionQueue | null>(null)
  const [counts, setCounts] = useState<NavCounts | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [checkedAt, setCheckedAt] = useState<Date | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const rows = await fetchAPI<HospitalListRow[]>('/admin/hospitals?skip=0&limit=200')
      setHospitals(Array.isArray(rows) ? rows : [])
      setCheckedAt(new Date())
    } catch (e: unknown) {
      if (!isExpectedOperatorRequestFailure(e)) throw e
      setError(safeOperatorError('onboarding', '대시보드 새로고침을 누르세요.'))
      return
    }
    // 아래는 보조 숫자다 — 하나가 실패해도 화면은 남은 것으로 그린다.
    const [metricRows, attentionRows, navCounts] = await Promise.allSettled([
      fetchAPI<unknown>('/admin/hospitals/month-metrics'),
      fetchAPI<AttentionQueue>('/admin/operations/attention'),
      fetchAPI<NavCounts>('/admin/operations/nav-counts'),
    ])
    setMetrics(metricRows.status === 'fulfilled' ? metricsByHospital(metricRows.value) : new Map())
    setAttention(attentionRows.status === 'fulfilled' ? attentionRows.value : null)
    setCounts(navCounts.status === 'fulfilled' ? navCounts.value : null)
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const reports = attention?.reports ?? null
  const summary = useMemo(
    () => (hospitals ? summarizeDashboard(hospitals, metrics, reports, counts) : null),
    [hospitals, metrics, reports, counts],
  )
  const lines = useMemo(() => (hospitals ? todoLines(hospitals, reports) : []), [hospitals, reports])
  const tableRows = useMemo(
    () => (hospitals ?? []).filter((h) => h.status !== 'PAUSED').slice(0, TABLE_VISIBLE),
    [hospitals],
  )

  return (
    <div className="p-4 sm:p-6 lg:p-8">
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{COPY.title}</h1>
          <p className="mt-1 text-sm text-slate-500">
            {checkedAt ? `${checkedAt.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' })} ${COPY.asOf}` : COPY.loading}
            {summary ? ` · ${COPY.hospitalCount.replace('{count}', String(summary.total))}` : ''}
          </p>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={() => void load()} className="min-h-11 rounded-lg border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 hover:bg-slate-50">
            {COPY.refresh}
          </button>
          <Link href="/hospitals/new" className="inline-flex min-h-11 items-center rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700">
            계약 등록
          </Link>
        </div>
      </div>

      {error && <OperatorIssuePanel message={error} surface="onboarding" onRetry={() => void load()} retryLabel={COPY.refresh} />}

      {summary && (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <NumberCard href="/hospitals" label={COPY.cardLive} value={summary.live} unit="곳" emphasis
              sub={COPY.cardLiveSub.replace('{preparing}', String(summary.preparing)).replace('{paused}', String(summary.paused))} />
            <NumberCard href="/operations" label={COPY.cardTodo} value={summary.operatorTodo} unit="건" sub={COPY.cardTodoSub} />
            <NumberCard href="/hospitals" label={COPY.cardReports.replace('{month}', String(reports?.period_month ?? ''))} value={summary.reportGapHospitals} unit="곳" sub={COPY.cardReportsSub} />
            <NumberCard href="/hospitals" label={ADMIN_COPY.visualItemsMissing} value={summary.visualMissingHospitals} unit="곳" sub={COPY.cardVisualSub} />
            <NumberCard href="/leads" label={COPY.cardLeads} value={summary.leadTotal} unit="건"
              sub={summary.leadNeedsAttention === null ? '' : COPY.cardLeadsSub.replace('{count}', String(summary.leadNeedsAttention))} />
          </div>

          <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
            <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-labelledby="dash-todo">
              <div className="flex items-center justify-between gap-2">
                <h2 id="dash-todo" className="text-base font-semibold text-slate-900">
                  {COPY.todoTitle} <span className="ml-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">{lines.length}</span>
                </h2>
                <Link href="/operations" className="text-xs font-semibold text-blue-700 hover:underline">{COPY.todoAll}</Link>
              </div>
              <p className="mt-1 text-xs text-slate-500">{COPY.todoHint}</p>
              {lines.length === 0 ? (
                <p className="mt-4 text-sm text-slate-500">{COPY.todoEmpty}</p>
              ) : (
                <ul className="mt-3 divide-y divide-slate-100">
                  {lines.slice(0, TODO_VISIBLE).map((line) => (
                    <li key={line.key} className="flex items-center gap-3 py-2.5">
                      <span aria-hidden className={`h-8 w-1 shrink-0 rounded-full ${TONE_BAR[line.tone]}`} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold text-slate-900">{line.hospitalName}</p>
                        <p className="truncate text-xs text-slate-600">{line.title}</p>
                      </div>
                      <Link href={line.href} className="inline-flex min-h-9 shrink-0 items-center rounded-lg border border-slate-300 px-3 text-xs font-semibold text-slate-700 hover:bg-slate-50">
                        {line.action}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
              {lines.length > TODO_VISIBLE && (
                <p className="mt-2 text-xs text-slate-500">{COPY.todoMore.replace('{count}', String(lines.length - TODO_VISIBLE))}</p>
              )}
            </section>

            <div className="grid gap-4">
              <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-labelledby="dash-status">
                <h2 id="dash-status" className="text-base font-semibold text-slate-900">{COPY.statusTitle}</h2>
                <p className="mt-2 text-2xl font-bold text-slate-900">{summary.total}<span className="ml-1 text-sm font-medium text-slate-500">곳</span></p>
                <StatusBar live={summary.live} preparing={summary.preparing} paused={summary.paused} total={summary.total} />
              </section>
              <section className="rounded-xl border border-slate-200 bg-white p-4 sm:p-5" aria-labelledby="dash-month">
                <h2 id="dash-month" className="text-base font-semibold text-slate-900">{COPY.monthTitle}</h2>
                <p className="mt-2 text-2xl font-bold tabular-nums text-slate-900">
                  {summary.monthPublic}<span className="ml-1 text-sm font-medium text-slate-500">/ {summary.monthPlanned}편</span>
                </p>
                <p className="mt-1 text-xs text-slate-500">{COPY.monthHint}</p>
              </section>
            </div>
          </div>

          <section className="mt-4 rounded-xl border border-slate-200 bg-white" aria-labelledby="dash-table">
            <div className="flex items-center justify-between px-4 pt-4 sm:px-5">
              <h2 id="dash-table" className="text-base font-semibold text-slate-900">{COPY.tableTitle}</h2>
              <Link href="/hospitals" className="text-xs font-semibold text-blue-700 hover:underline">{COPY.tableAll}</Link>
            </div>
            <div className="admin-responsive-table-wrap mt-2 overflow-x-auto">
              <table className="admin-responsive-table w-full min-w-[760px] text-sm">
                <thead className="border-b border-slate-200 bg-slate-50">
                  <tr>
                    <th className="px-5 py-2.5 text-left font-medium text-slate-600">병원</th>
                    <th className="px-4 py-2.5 text-left font-medium text-slate-600">{ADMIN_COPY.hospitalList.colAddress}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-slate-600">{ADMIN_COPY.hospitalList.colPublish}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-slate-600">{ADMIN_COPY.hospitalList.colMention}</th>
                    <th className="px-4 py-2.5 text-left font-medium text-slate-600">{ADMIN_COPY.hospitalList.colTodo}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {tableRows.map((h) => {
                    const addresses = readPublicAddresses(h)
                    const shown = addresses.live ?? addresses.custom ?? addresses.platform
                    const progress = publishProgress(metrics.get(h.id))
                    return (
                      <tr key={h.id}>
                        <td className="px-5 py-3" data-primary="true">
                          <Link href={`/hospitals/${h.id}`} className="font-medium text-slate-900 hover:text-blue-700">{h.name}</Link>
                        </td>
                        <td className="px-4 py-3 text-xs" data-label={ADMIN_COPY.hospitalList.colAddress}>
                          {shown ? <AddressLink address={shown} className="max-w-[220px]" /> : <span className="text-slate-400">—</span>}
                        </td>
                        <td className="px-4 py-3 text-xs tabular-nums text-slate-600" data-label={ADMIN_COPY.hospitalList.colPublish}>
                          {progress ? `${ADMIN_COPY.hospitalList.publishPrefix} ${progress.label}` : '—'}
                        </td>
                        <td className="px-4 py-3 text-xs tabular-nums text-slate-700" data-label={ADMIN_COPY.hospitalList.colMention}>
                          {mentionRateLabel(metrics.get(h.id))}
                        </td>
                        <td className="px-4 py-3 text-xs" data-label={ADMIN_COPY.hospitalList.colTodo}>
                          {h.open_exception_count > 0 ? (
                            <span className="rounded-full bg-amber-50 px-2 py-0.5 font-semibold text-amber-800">예외 {h.open_exception_count}건</span>
                          ) : (
                            <span className="text-slate-400">—</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
          <p className="mt-3 text-[11px] text-slate-400">{COPY.footnote}</p>
        </>
      )}
    </div>
  )
}

function NumberCard({
  href, label, value, unit, sub, emphasis = false,
}: {
  href: string
  label: string
  value: number | null
  unit: string
  sub: string
  emphasis?: boolean
}) {
  return (
    <Link
      href={href}
      className={`block rounded-xl border p-4 transition-colors ${
        emphasis ? 'border-blue-200 bg-blue-50 hover:bg-blue-100' : 'border-slate-200 bg-white hover:bg-slate-50'
      }`}
    >
      <p className="text-xs font-medium text-slate-600">{label}</p>
      <p className="mt-1 text-2xl font-bold tabular-nums text-slate-900">
        {value === null ? '—' : value}
        <span className="ml-1 text-sm font-medium text-slate-500">{value === null ? '' : unit}</span>
      </p>
      {sub && <p className="mt-0.5 text-[11px] text-slate-500">{sub}</p>}
    </Link>
  )
}

function StatusBar({ live, preparing, paused, total }: { live: number; preparing: number; paused: number; total: number }) {
  const width = (value: number) => (total > 0 ? `${(value / total) * 100}%` : '0%')
  return (
    <>
      <div className="mt-3 flex h-2 overflow-hidden rounded-full bg-slate-100" aria-hidden>
        <span className="bg-emerald-500" style={{ width: width(live) }} />
        <span className="bg-blue-500" style={{ width: width(preparing) }} />
        <span className="bg-slate-400" style={{ width: width(paused) }} />
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-600">
        <li><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-500" />{ADMIN_COPY.dashboardScreen.legendLive} {live}</li>
        <li><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-blue-500" />준비 중 {preparing}</li>
        <li><span aria-hidden className="mr-1 inline-block h-2 w-2 rounded-full bg-slate-400" />일시 정지 {paused}</li>
      </ul>
    </>
  )
}
