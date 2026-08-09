'use client'

import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import { fetchAPI } from '@/lib/api'
import { parseMonthValue, previousMonthValue } from '@/lib/report-period'
import {
  getCustomerReportDownload,
  getInternalReportLabel,
  isEffectivelyDelivered,
  readReportDeliveryState,
  type ReportDeliveryInput,
} from '@/lib/report-delivery'

interface Report {
  id: string
  hospital_id: string
  period_year: number
  period_month: number
  report_type: string
  display?: {
    report_type_label?: string | null
    screening_status?: ScreeningStatus | string | null
    screening_status_label?: string | null
    pdf_status?: string | null
    pdf_status_label?: string | null
  }
  has_pdf: boolean
  /** 원장에게 그대로 전달하는 1페이지 판본이 준비됐는지. */
  has_doctor_pdf?: boolean
  doctor_artifact_state?: 'MISSING' | 'INVALID' | 'VALID'
  doctor_artifact_sha256?: string | null
  download_url: string | null
  created_at: string
  sent_at: string | null
  delivery_ready?: boolean
  delivery_blockers?: string[]
  customer_ready?: boolean
  delivery_history?: Array<Record<string, unknown>>
  effective_delivery?: ({ event_type?: string | null } & Record<string, unknown>) | null
  sov_summary?: Record<string, unknown> | null
  content_summary?: Record<string, unknown> | null
  essence_summary?: Record<string, unknown> | null
}

const TYPE_LABELS: Record<string, string> = {
  V0: 'V0 진단',
  MONTHLY: '월간 리포트',
}

type ScreeningStatus = 'PDF_PENDING' | 'AWAITING_REVIEW' | 'DELIVERED'

const SCREENING_LABELS: Record<ScreeningStatus, { label: string; cls: string }> = {
  PDF_PENDING: {
    label: '내부 리포트 생성 중',
    cls: 'bg-[var(--color-revisit-coolgrey-90)] text-[var(--color-revisit-text-helper)]',
  },
  AWAITING_REVIEW: {
    label: '검수 대기',
    cls: 'bg-[var(--color-revisit-primary-95)] text-[var(--color-revisit-nav)]',
  },
  DELIVERED: {
    label: '전달 완료',
    cls: 'bg-[color-mix(in_srgb,var(--color-revisit-green-50)_12%,white)] text-[var(--color-revisit-green-50)]',
  },
}

const REPORT_DRAWER_STYLE = {
  overlay: 'fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-[color-mix(in_srgb,var(--color-revisit-nav)_55%,transparent)] p-4',
  surface: 'my-8 w-full max-w-2xl rounded-xl bg-white',
  header: 'flex items-start justify-between gap-3 border-b border-[var(--color-revisit-coolgrey-20)] p-6',
  heading: 'text-base font-bold text-[var(--color-revisit-text-title)] [word-break:keep-all] sm:text-lg',
  helper: 'text-[var(--color-revisit-text-helper)]',
  muted: 'text-[var(--color-revisit-text-caption)]',
  close: 'min-h-11 min-w-11 rounded-lg text-xl text-[var(--color-revisit-text-helper)] hover:bg-[var(--color-revisit-coolgrey-90)]',
  panel: 'rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white p-4',
  softPanel: 'rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-[var(--color-revisit-coolgrey-90)] p-4',
  infoPanel: 'rounded-lg border border-[var(--color-revisit-primary-80)] bg-[var(--color-revisit-primary-95)] p-4',
  sectionTitle: 'mb-3 text-sm font-semibold text-[var(--color-revisit-text-title)]',
  primaryAction: 'block min-h-11 w-full rounded-lg bg-[var(--color-revisit-nav)] px-4 py-3 text-center text-sm font-semibold text-white hover:opacity-90',
  secondaryAction: 'block min-h-11 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white px-4 py-3 text-center text-sm font-medium text-[var(--color-revisit-text-helper)] hover:bg-[var(--color-revisit-coolgrey-90)]',
  control: 'mt-1 min-h-11 w-full rounded-md border border-[var(--color-revisit-coolgrey-20)] bg-white px-3 text-[var(--color-revisit-text-title)]',
  success: 'rounded-lg border border-[var(--color-revisit-green-50)] bg-white text-[var(--color-revisit-green-50)]',
  danger: 'rounded-lg border border-[var(--color-revisit-red-50)] bg-white text-[var(--color-revisit-red-50)]',
} as const

function getScreeningStatus(r: Report): ScreeningStatus {
  const displayStatus = r.display?.screening_status
  if (displayStatus === 'DELIVERED' || displayStatus === 'PDF_PENDING' || displayStatus === 'AWAITING_REVIEW') {
    return displayStatus
  }
  if (isEffectivelyDelivered(r)) return 'DELIVERED'
  if (!r.download_url && !r.has_pdf) return 'PDF_PENDING'
  return 'AWAITING_REVIEW'
}

function getScreeningMeta(r: Report): { label: string; cls: string } {
  const status = getScreeningStatus(r)
  const fallback = SCREENING_LABELS[status]
  return { ...fallback, label: r.display?.screening_status_label ?? fallback.label }
}

function getReportTypeLabel(r: Report): string {
  return r.display?.report_type_label ?? TYPE_LABELS[r.report_type] ?? r.report_type
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '-'
  try {
    return new Date(value).toLocaleDateString('ko-KR')
  } catch {
    return value
  }
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function asNumber(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v
  if (typeof v === 'string' && v.trim() !== '' && !Number.isNaN(Number(v))) return Number(v)
  return null
}

function asString(v: unknown): string | null {
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  return null
}

const SUMMARY_LABEL_MAP: Record<string, string> = {
  sov_pct: '통합 AI 답변 언급률',
  prev_sov_pct: '전월 AI 답변 언급률',
  change_pct: 'AI 답변 언급률 변화',
  chatgpt: 'ChatGPT',
  gemini: 'Gemini',
  overall: '통합',
  published_count: '발행 콘텐츠 수',
  generated_count: '생성 콘텐츠 수',
}

function humanizeKey(k: string): string {
  return SUMMARY_LABEL_MAP[k] ?? k.replace(/_/g, ' ')
}

function renderSummaryValue(key: string, value: unknown): string {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'boolean') return value ? '예' : '아니오'
  const num = asNumber(value)
  if (num !== null) {
    if (key.endsWith('_pct') || key === 'overall' || key === 'chatgpt' || key === 'gemini') {
      const sign = key === 'change_pct' && num > 0 ? '+' : ''
      return `${sign}${num.toFixed(1)}%`
    }
    return Number.isInteger(num) ? String(num) : num.toFixed(2)
  }
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return `${value.length}개 항목`
  if (isPlainObject(value)) return `${Object.keys(value).length}개 항목`
  return '-'
}

function SummaryGrid({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined)
  if (entries.length === 0) {
    return <p className={`text-sm ${REPORT_DRAWER_STYLE.muted}`}>표시할 항목이 없습니다.</p>
  }
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-2 text-sm">
          <span className={REPORT_DRAWER_STYLE.helper}>{humanizeKey(k)}</span>
          <span className="font-medium text-[var(--color-revisit-text-title)]">{renderSummaryValue(k, v)}</span>
        </div>
      ))}
    </div>
  )
}

function ChecklistRow({ ok, label, hint }: { ok: boolean; label: string; hint?: string }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <span
        className={`mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-xs font-bold ${
          ok
            ? 'bg-[color-mix(in_srgb,var(--color-revisit-green-50)_12%,white)] text-[var(--color-revisit-green-50)]'
            : 'bg-[var(--color-revisit-primary-95)] text-[var(--color-revisit-nav)]'
        }`}
      >
        {ok ? '✓' : '!'}
      </span>
      <div className="flex-1">
        <div className={ok ? 'text-[var(--color-revisit-text-title)]' : 'font-medium text-[var(--color-revisit-nav)]'}>{label}</div>
        {hint && <div className={`mt-0.5 text-xs ${REPORT_DRAWER_STYLE.helper}`}>{hint}</div>}
      </div>
    </div>
  )
}

// 백엔드 계약(추가 예정): POST /admin/hospitals/{id}/reports/{report_id}/mark-sent
// → MonthlyReport.sent_at이 기록된 갱신 리포트를 반환.
// 실제 경로/메서드가 달라지면 이 함수 한 곳만 수정하면 된다.
function requestMarkSent(
  hospitalId: string,
  reportId: string,
  input: ReportDeliveryInput,
): Promise<Report> {
  return fetchAPI<Report>(`/admin/hospitals/${hospitalId}/reports/${reportId}/mark-sent`, {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

const defaultGeneratePeriod = previousMonthValue()

export default function ReportsPage() {
  const { id } = useParams<{ id: string }>()
  const [reports, setReports] = useState<Report[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Report | null>(null)
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [markingSent, setMarkingSent] = useState(false)
  const [markSentError, setMarkSentError] = useState<string | null>(null)
  // 기본값은 지난달 — 월말 배치 실패는 대개 달이 바뀐 뒤에 발견된다.
  const [generatePeriod, setGeneratePeriod] = useState(defaultGeneratePeriod)
  const [generating, setGenerating] = useState(false)
  const [generateMessage, setGenerateMessage] = useState<string | null>(null)
  const [generateError, setGenerateError] = useState<string | null>(null)

  useEffect(() => {
    fetchAPI<Report[]>(`/admin/hospitals/${id}/reports`)
      .then(setReports)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  async function openDetail(report: Report) {
    setDetailError(null)
    setMarkSentError(null)
    setDetailLoadingId(report.id)
    try {
      const full = await fetchAPI<Report>(`/admin/hospitals/${id}/reports/${report.id}`)
      setSelected(full)
    } catch (e: unknown) {
      setDetailError(e instanceof Error ? e.message : '리포트 상세 정보를 불러오지 못했습니다.')
      setSelected(null)
    } finally {
      setDetailLoadingId(null)
    }
  }

  async function handleMarkSent(report: Report, input: ReportDeliveryInput) {
    if (markingSent || isEffectivelyDelivered(report)) return
    if (!readReportDeliveryState(report).ready) {
      setMarkSentError('백엔드 전달 준비 검사를 통과한 리포트만 완료로 표시할 수 있습니다.')
      return
    }
    setMarkSentError(null)
    setMarkingSent(true)
    try {
      const updated = await requestMarkSent(id, report.id, input)
      if (updated) {
        setReports((prev) => prev.map((r) => (r.id === report.id ? { ...r, ...updated } : r)))
        setSelected((prev) => (prev && prev.id === report.id ? { ...prev, ...updated } : prev))
      }
    } catch (e: unknown) {
      setMarkSentError(e instanceof Error ? e.message : '전달 완료 처리에 실패했습니다.')
    } finally {
      setMarkingSent(false)
    }
  }

  async function handleGenerateMonthly() {
    setGenerateMessage(null)
    setGenerateError(null)
    const parsed = parseMonthValue(generatePeriod)
    if (!parsed) {
      setGenerateError('생성할 월을 선택해 주세요.')
      return
    }
    const { year, month } = parsed
    setGenerating(true)
    try {
      await fetchAPI(
        `/admin/hospitals/${id}/operations/generate-monthly-report?year=${year}&month=${month}`,
        { method: 'POST' },
      )
      setGenerateMessage(
        `${year}년 ${month}월 리포트 생성을 요청했습니다. 완료되면 Slack으로 알려드립니다. ` +
          '이 목록에는 새로고침해야 나타납니다. 이미 있는 달은 새로 만들지 않습니다.',
      )
    } catch (e: unknown) {
      setGenerateError(e instanceof Error ? e.message : '리포트 생성 요청에 실패했습니다.')
    } finally {
      setGenerating(false)
    }
  }

  const stats = useMemo(() => {
    const now = new Date()
    const y = now.getFullYear()
    const m = now.getMonth() + 1
    let awaiting = 0
    let pdfReady = 0
    let delivered = 0
    let thisMonth = 0
    for (const r of reports) {
      const s = getScreeningStatus(r)
      if (s === 'AWAITING_REVIEW') awaiting += 1
      if (s === 'DELIVERED') delivered += 1
      if (r.has_pdf || r.download_url) pdfReady += 1
      if (r.period_year === y && r.period_month === m) thisMonth += 1
    }
    return { awaiting, pdfReady, delivered, thisMonth }
  }, [reports])

  return (
    <div className="p-4 sm:p-6 lg:p-8">
      <div className="mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h2 className="text-xl font-bold text-slate-900">리포트 검수</h2>
          <p className="mt-1 text-sm text-slate-600">
            PDF를 내려받기 전에 AI 답변 노출, 콘텐츠 성과, 운영 기준 검수 결과를 먼저 확인합니다.
          </p>
        </div>
        {/* 월말 배치가 실패하면 그 달 리포트가 통째로 비어 있게 된다 — 그때 AE가
            개발자 없이 다시 만드는 경로. 이미 있는 달은 덮어쓰지 않는다. */}
        <div className="shrink-0 rounded-xl border border-slate-200 bg-white p-3">
          <p className="text-xs font-medium text-slate-700">월간 리포트가 없나요?</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <input
              type="month"
              value={generatePeriod}
              // 이번 달 이후는 백엔드가 거부한다 — 빈 리포트 행이 월말 배치를 막기 때문에.
              max={defaultGeneratePeriod}
              onChange={(e) => setGeneratePeriod(e.target.value)}
              className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
              aria-label="생성할 리포트 월"
            />
            <button
              type="button"
              onClick={() => void handleGenerateMonthly()}
              disabled={generating}
              className="rounded-lg bg-slate-900 px-3 py-1.5 text-sm font-semibold text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {generating ? '요청 중...' : '리포트 생성'}
            </button>
          </div>
          {generateMessage && (
            <p role="status" className="mt-2 max-w-xs text-xs text-emerald-700">{generateMessage}</p>
          )}
          {generateError && (
            <p role="alert" className="mt-2 max-w-xs text-xs text-red-600">{generateError}</p>
          )}
        </div>
      </div>

      {!loading && !error && (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          <SummaryCard label="검수 대기" value={stats.awaiting} tone="blue" />
          <SummaryCard label="내부 리포트 준비" value={stats.pdfReady} tone="indigo" />
          <SummaryCard label="전달 완료" value={stats.delivered} tone="green" />
          <SummaryCard label="이번 달 리포트" value={stats.thisMonth} tone="gray" />
        </div>
      )}

      {loading && <div className="text-center py-16 text-slate-500">불러오는 중...</div>}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">오류: {error}</div>
      )}

      {detailError && (
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded-lg p-4 text-amber-800 text-sm">
          리포트 상세를 불러오지 못했습니다. 원장 보고 전 검수 데이터가 불완전할 수 있습니다. ({detailError})
        </div>
      )}

      {!loading && !error && (
        <div className="admin-responsive-table-wrap overflow-hidden rounded-xl border border-slate-200 bg-white">
          <table className="admin-responsive-table w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-6 py-3 text-slate-600 font-medium">기간</th>
                <th className="text-left px-6 py-3 text-slate-600 font-medium">리포트 유형</th>
                <th className="text-left px-6 py-3 text-slate-600 font-medium">검수 상태</th>
                <th className="text-center px-6 py-3 text-slate-600 font-medium">AE 내부 리포트</th>
                <th className="text-left px-6 py-3 text-slate-600 font-medium">생성일</th>
                <th className="text-right px-6 py-3 text-slate-600 font-medium">액션</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {reports.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-6 py-10">
                    <EmptyReportState />
                  </td>
                </tr>
              )}
              {reports.map((r) => {
                const status = getScreeningStatus(r)
                const meta = getScreeningMeta(r)
                return (
                  <tr key={r.id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-6 py-4 text-slate-900 font-medium" data-primary="true">
                      {r.period_year}년 {r.period_month}월
                    </td>
                    <td className="px-6 py-4 text-slate-600" data-label="유형">
                      {getReportTypeLabel(r)}
                    </td>
                    <td className="px-6 py-4" data-label="검수 상태">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${meta.cls}`}>
                        {meta.label}
                      </span>
                      {!r.sent_at && r.delivery_ready === false && (
                        <p className="mt-1 text-[11px] font-medium text-amber-700">
                          전달 차단 {r.delivery_blockers?.length ?? 0}건
                        </p>
                      )}
                    </td>
                    <td className="px-6 py-4 text-center" data-label="AE 내부 리포트">
                      {r.download_url ? (
                        <a
                          href={r.download_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex min-h-11 items-center rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white px-3 text-xs font-semibold text-[var(--color-revisit-text-helper)] hover:bg-[var(--color-revisit-coolgrey-90)]"
                        >
                          {getInternalReportLabel(r)}
                        </a>
                      ) : r.has_pdf ? (
                        <span className="text-xs text-[var(--color-revisit-text-helper)]">{getInternalReportLabel(r)}</span>
                      ) : (
                        <span className="text-xs text-[var(--color-revisit-text-caption)]">{getInternalReportLabel(r)}</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-slate-600" data-label="생성일">
                      <div>{formatDate(r.created_at)}</div>
                      {r.sent_at && <div className="text-xs text-green-700 mt-0.5">전달 {formatDate(r.sent_at)}</div>}
                    </td>
                    <td className="px-6 py-4 text-right" data-label="액션">
                      <button
                        onClick={() => openDetail(r)}
                        disabled={detailLoadingId === r.id}
                        className="px-3 py-1 bg-slate-900 text-white text-xs rounded hover:bg-slate-700 disabled:opacity-60"
                      >
                        {detailLoadingId === r.id ? '불러오는 중' : status === 'DELIVERED' ? '보기' : '검수하기'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <DetailDrawer
          report={selected}
          onClose={() => setSelected(null)}
          onMarkSent={(input) => handleMarkSent(selected, input)}
          markingSent={markingSent}
          markSentError={markSentError}
        />
      )}
    </div>
  )
}

function SummaryCard({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone: 'blue' | 'green' | 'indigo' | 'gray'
}) {
  const toneCls: Record<string, string> = {
    blue: 'border-blue-200 bg-blue-50 text-blue-900',
    green: 'border-green-200 bg-green-50 text-green-900',
    indigo: 'border-indigo-200 bg-indigo-50 text-indigo-900',
    gray: 'border-slate-200 bg-slate-50 text-slate-900',
  }
  return (
    <div className={`rounded-xl border p-4 ${toneCls[tone]}`}>
      <div className="text-xs font-medium opacity-80">{label}</div>
      <div className="mt-1 text-2xl font-bold">{value}</div>
    </div>
  )
}

function EmptyReportState() {
  return (
    <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50 px-6 py-8 text-center">
      <p className="text-sm font-semibold text-slate-800">아직 검수할 리포트가 없습니다.</p>
      <p className="mt-2 text-sm leading-6 text-slate-500">
        병원 자료와 콘텐츠 운영 기준을 검토한 뒤 AI 언급률 측정과 콘텐츠 성과가 쌓이면 리포트가 생성됩니다.
      </p>
      <div className="mt-4 grid gap-2 text-left text-xs text-slate-600 md:grid-cols-3">
        <span className="rounded-lg bg-white px-3 py-2 ring-1 ring-slate-200">1. 운영 기준 승인 확인</span>
        <span className="rounded-lg bg-white px-3 py-2 ring-1 ring-slate-200">2. AI 언급률 측정 실행</span>
        <span className="rounded-lg bg-white px-3 py-2 ring-1 ring-slate-200">3. 발행 콘텐츠 성과 확인</span>
      </div>
    </div>
  )
}

function ReportGuidance({
  missingItems,
  recommendedActions,
  medicalRiskCount,
}: {
  missingItems: string[]
  recommendedActions: string[]
  medicalRiskCount: number
}) {
  const hasGuidance = missingItems.length > 0 || recommendedActions.length > 0 || medicalRiskCount > 0
  if (!hasGuidance) {
    return (
      <div className={`${REPORT_DRAWER_STYLE.success} p-3 text-sm`}>
        원장님께 전달하기 전 필수 요약은 모두 준비되어 있습니다. 원장 전달용 리포트 내용만 최종 확인하면 됩니다.
      </div>
    )
  }

  return (
    <div className={`${REPORT_DRAWER_STYLE.infoPanel} p-3`}>
      <div className="text-xs font-semibold text-[var(--color-revisit-nav)]">전달 전 보완할 항목</div>
      {missingItems.length > 0 && (
        <ul className="mt-2 list-inside list-disc space-y-0.5 text-sm text-[var(--color-revisit-nav)]">
          {missingItems.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
      {medicalRiskCount > 0 && (
        <p className="mt-2 text-sm text-[var(--color-revisit-nav)]">의료광고 리스크 {medicalRiskCount}건은 원장 전달용 리포트 전달 전 표현 수정 여부를 확인해야 합니다.</p>
      )}
      {recommendedActions.length > 0 && (
        <div className="mt-3 rounded-md bg-white/70 p-2">
          <div className="text-xs font-semibold text-[var(--color-revisit-nav)]">권장 조치</div>
          <ul className="mt-1 list-inside list-disc space-y-0.5 text-sm text-[var(--color-revisit-nav)]">
            {recommendedActions.map((action, i) => (
              <li key={i}>{action}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function DetailDrawer({
  report,
  onClose,
  onMarkSent,
  markingSent,
  markSentError,
}: {
  report: Report
  onClose: () => void
  onMarkSent: (input: ReportDeliveryInput) => void
  markingSent: boolean
  markSentError: string | null
}) {
  const meta = getScreeningMeta(report)
  const sov = isPlainObject(report.sov_summary) ? report.sov_summary : null
  const content = isPlainObject(report.content_summary) ? report.content_summary : null
  const essence = isPlainObject(report.essence_summary) ? report.essence_summary : null

  const recommendedActions = essence && Array.isArray(essence.recommended_actions)
    ? (essence.recommended_actions as unknown[]).map((v) => String(v))
    : []
  const medicalRiskFindings = essence && Array.isArray(essence.medical_risk_findings)
    ? (essence.medical_risk_findings as Array<Record<string, unknown>>)
    : []
  const needsReviewCount = essence ? asNumber(essence.needs_review_content_count) ?? 0 : 0
  const missingStandardCount = essence ? asNumber(essence.missing_philosophy_content_count) ?? 0 : 0
  const alignedContentCount = essence ? asNumber(essence.aligned_content_count) ?? 0 : 0
  const processedSourceCount = essence ? asNumber(essence.processed_source_count) ?? 0 : 0
  const totalSourceCount = essence ? asNumber(essence.source_count) ?? 0 : 0
  const { ready: deliveryReady, blockers: deliveryBlockers } = readReportDeliveryState(report)
  const customerDownload = getCustomerReportDownload(report)
  const [recipientLabel, setRecipientLabel] = useState('')
  const [channel, setChannel] = useState('대면')
  const [deliveryNote, setDeliveryNote] = useState('')
  const canSubmitDelivery = Boolean(
    deliveryReady && report.doctor_artifact_sha256 && recipientLabel.trim() && channel.trim(),
  )
  const effectivelyDelivered = isEffectivelyDelivered(report)

  return (
    <div className={REPORT_DRAWER_STYLE.overlay}>
      <div className={REPORT_DRAWER_STYLE.surface} role="dialog" aria-modal="true" aria-labelledby="report-drawer-title">
        <div className={REPORT_DRAWER_STYLE.header}>
          <div className="min-w-0 flex-1">
            <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center">
              <h3 id="report-drawer-title" className={REPORT_DRAWER_STYLE.heading}>
                {getReportTypeLabel(report)} — {report.period_year}년 {report.period_month}월
              </h3>
              <span className={`inline-flex shrink-0 items-center whitespace-nowrap px-2 py-0.5 rounded text-xs font-medium ${meta.cls}`}>
                {meta.label}
              </span>
            </div>
            <div className={`mt-1 text-xs ${REPORT_DRAWER_STYLE.helper}`}>
              생성 {formatDate(report.created_at)}
              {report.sent_at ? ` · 전달 ${formatDate(report.sent_at)}` : ''}
            </div>
          </div>
          <button onClick={onClose} className={REPORT_DRAWER_STYLE.close} aria-label="닫기">
            ✕
          </button>
        </div>

        <div className="p-6 space-y-6">
          <section className={REPORT_DRAWER_STYLE.panel}>
            <h4 className={REPORT_DRAWER_STYLE.sectionTitle}>원장 보고 전 체크</h4>
            <div className="space-y-3">
              <ReportGuidance
                missingItems={deliveryBlockers}
                recommendedActions={recommendedActions}
                medicalRiskCount={medicalRiskFindings.length}
              />
              <div className="space-y-2">
                <ChecklistRow
                  ok={Boolean(report.download_url || report.has_pdf)}
                  label="내부 리포트 준비"
                  hint={report.download_url
                    ? 'AE 검수 전용 · 고객 전달 금지'
                    : 'AE 내부 리포트 생성이 완료되면 검수 링크가 활성화됩니다. 고객 전달 금지'}
                />
                <ChecklistRow ok={Boolean(sov)} label="AI 답변 언급률 요약 존재" hint={sov ? undefined : '환자 질문 측정 결과를 먼저 확인하세요.'} />
                {report.report_type === 'MONTHLY' && (
                  <>
                    <ChecklistRow ok={Boolean(content)} label="콘텐츠 성과 요약 존재" hint={content ? undefined : '발행 콘텐츠 수와 성과 요약을 먼저 확인하세요.'} />
                    <ChecklistRow ok={Boolean(essence)} label="운영 기준 요약 존재" hint={essence ? undefined : '승인된 운영 기준과 자료 검토 상태를 먼저 확인하세요.'} />
                  </>
                )}
              </div>
              {essence && (
                <div className={`mt-3 ${REPORT_DRAWER_STYLE.softPanel}`}>
                  <div className="mb-2 text-xs font-semibold text-[var(--color-revisit-text-helper)]">내부 리포트 확인 전 먼저 볼 운영 기준 검수</div>
                  <div className="grid gap-2 text-sm md:grid-cols-2">
                    <ChecklistRow
                      ok={Boolean(essence.approved_philosophy_exists)}
                      label={essence.approved_philosophy_exists ? '승인된 콘텐츠 운영 기준 있음' : '승인된 콘텐츠 운영 기준 없음'}
                      hint={essence.approved_at ? `승인일 ${formatDate(asString(essence.approved_at))}` : undefined}
                    />
                    <ChecklistRow
                      ok={processedSourceCount > 0 && processedSourceCount === totalSourceCount}
                      label={`검토된 병원 자료 ${processedSourceCount}/${totalSourceCount}`}
                      hint={processedSourceCount === totalSourceCount ? undefined : '아직 검토가 끝나지 않은 병원 자료가 있습니다.'}
                    />
                    <ChecklistRow
                      ok={needsReviewCount === 0 && missingStandardCount === 0}
                      label={`재검토 필요 콘텐츠 ${needsReviewCount + missingStandardCount}건`}
                      hint={alignedContentCount ? `운영 기준에 맞는 콘텐츠 ${alignedContentCount}건` : undefined}
                    />
                    <ChecklistRow
                      ok={medicalRiskFindings.length === 0}
                      label={`의료광고 리스크 ${medicalRiskFindings.length}건`}
                      hint={medicalRiskFindings.length ? '원장님께 전달하기 전 표현 수정 여부를 확인하세요.' : undefined}
                    />
                  </div>
                </div>
              )}
            </div>
          </section>

          <section>
            <h4 className={REPORT_DRAWER_STYLE.sectionTitle}>이번 달 핵심 변화</h4>
            <div className="grid gap-3 md:grid-cols-2">
              <div className={REPORT_DRAWER_STYLE.infoPanel}>
                <p className="mb-2 text-xs font-semibold text-[var(--color-revisit-nav)]">AI 답변 언급률</p>
                {sov ? <SummaryGrid data={sov} /> : <p className={`text-sm ${REPORT_DRAWER_STYLE.muted}`}>데이터 없음</p>}
              </div>
              <div className={REPORT_DRAWER_STYLE.softPanel}>
                <p className="mb-2 text-xs font-semibold text-[var(--color-revisit-text-helper)]">콘텐츠 성과</p>
                {content ? <SummaryGrid data={content} /> : <p className={`text-sm ${REPORT_DRAWER_STYLE.muted}`}>데이터 없음</p>}
              </div>
            </div>
          </section>

          {essence ? (
            <section>
              <h4 className={REPORT_DRAWER_STYLE.sectionTitle}>콘텐츠 운영 기준</h4>
              <div className={`${REPORT_DRAWER_STYLE.panel} space-y-3`}>
                <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                  <EssenceRow
                    label="승인된 운영 기준"
                    value={
                      essence.approved_philosophy_exists
                        ? `v${asNumber(essence.philosophy_version) ?? '-'}${
                            essence.approved_at ? ` · 승인 ${formatDate(asString(essence.approved_at))}` : ''
                          }`
                        : '미승인'
                    }
                    tone={essence.approved_philosophy_exists ? 'ok' : 'warn'}
                  />
                  <EssenceRow
                    label="자료 처리"
                    value={`${asNumber(essence.processed_source_count) ?? 0} / ${asNumber(essence.source_count) ?? 0}`}
                    tone={asNumber(essence.processed_source_count) ? 'ok' : 'warn'}
                  />
                  <EssenceRow
                    label="정합 콘텐츠"
                    value={String(asNumber(essence.aligned_content_count) ?? 0)}
                    tone="ok"
                  />
                  <EssenceRow
                    label="재검수 필요"
                    value={String(asNumber(essence.needs_review_content_count) ?? 0)}
                    tone={asNumber(essence.needs_review_content_count) ? 'warn' : 'ok'}
                  />
                  <EssenceRow
                    label="운영 기준 누락 콘텐츠"
                    value={String(asNumber(essence.missing_philosophy_content_count) ?? 0)}
                    tone={asNumber(essence.missing_philosophy_content_count) ? 'warn' : 'ok'}
                  />
                  <EssenceRow
                    label="자료 최신성"
                    value={essence.source_stale ? '변경됨 (재검토 필요)' : '최신'}
                    tone={essence.source_stale ? 'warn' : 'ok'}
                  />
                </div>

                {medicalRiskFindings.length > 0 && (
                  <div className={`${REPORT_DRAWER_STYLE.danger} p-3`}>
                    <div className="mb-1 text-xs font-semibold">
                      의료광고 리스크 {medicalRiskFindings.length}건
                    </div>
                    <ul className="space-y-1 text-sm">
                      {medicalRiskFindings.slice(0, 5).map((finding, i) => {
                        const title = asString(finding.title) ?? '(제목 없음)'
                        const violations = Array.isArray(finding.violations)
                          ? (finding.violations as unknown[]).map((v) => String(v)).join(', ')
                          : ''
                        return (
                          <li key={i} className="flex flex-col">
                            <span className="font-medium">{title}</span>
                            {violations && <span className="text-xs">금지 표현: {violations}</span>}
                          </li>
                        )
                      })}
                      {medicalRiskFindings.length > 5 && (
                        <li className="text-xs">외 {medicalRiskFindings.length - 5}건</li>
                      )}
                    </ul>
                  </div>
                )}
              </div>
            </section>
          ) : (
            <section>
              <h4 className={REPORT_DRAWER_STYLE.sectionTitle}>콘텐츠 운영 기준</h4>
              <div className={`${REPORT_DRAWER_STYLE.infoPanel} border-dashed text-sm text-[var(--color-revisit-nav)]`}>
                운영 기준 요약이 아직 리포트에 포함되지 않았습니다. 원장님께 전달하기 전 병원 자료 검토와 승인된 운영 기준 상태를 먼저 확인하세요.
              </div>
            </section>
          )}

          <section>
            <h4 className={REPORT_DRAWER_STYLE.sectionTitle}>원장 보고 자료</h4>
            {customerDownload ? (
              <div className="space-y-2">
                <p className={`${REPORT_DRAWER_STYLE.infoPanel} px-3 py-2 text-xs leading-5 text-[var(--color-revisit-nav)]`}>
                  검증된 원장 보고용 판본입니다. 아래 전달 기록의 PDF 해시와 같은 파일인지 확인하세요.
                </p>
                <a
                  href={customerDownload}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={REPORT_DRAWER_STYLE.primaryAction}
                >
                  원장 전달용 PDF 다운로드
                </a>
                {report.download_url && (
                  <a
                    href={report.download_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={REPORT_DRAWER_STYLE.secondaryAction}
                  >
                    AE 내부 리포트 다운로드 · 고객 전달 금지
                  </a>
                )}
              </div>
            ) : (
              <div className={`${REPORT_DRAWER_STYLE.softPanel} border-dashed py-4 text-center text-sm ${REPORT_DRAWER_STYLE.helper}`}>
                검증된 원장 전달용 PDF가 없습니다. 내부 검수용 PDF로 대신 전달할 수 없습니다.
              </div>
            )}

            {markSentError && (
              <p className={`mt-2 px-3 py-2 text-sm ${REPORT_DRAWER_STYLE.danger}`}>
                {markSentError}
              </p>
            )}
            {effectivelyDelivered ? (
              <p className={`mt-2 px-3 py-2 text-sm ${REPORT_DRAWER_STYLE.success}`}>
                원장 보고 완료 — 전달일 {formatDate(report.sent_at)}
              </p>
            ) : (
              <div className={`mt-3 space-y-3 ${REPORT_DRAWER_STYLE.panel}`}>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label className={`text-sm ${REPORT_DRAWER_STYLE.helper}`}>
                    수신자
                    <input
                      value={recipientLabel}
                      onChange={(event) => setRecipientLabel(event.target.value)}
                      placeholder="예: 김원장"
                      className={REPORT_DRAWER_STYLE.control}
                    />
                  </label>
                  <label className={`text-sm ${REPORT_DRAWER_STYLE.helper}`}>
                    전달 채널
                    <input
                      value={channel}
                      onChange={(event) => setChannel(event.target.value)}
                      placeholder="예: 대면"
                      className={REPORT_DRAWER_STYLE.control}
                    />
                  </label>
                </div>
                <label className={`block text-sm ${REPORT_DRAWER_STYLE.helper}`}>
                  메모 (선택)
                  <input
                    value={deliveryNote}
                    onChange={(event) => setDeliveryNote(event.target.value)}
                    placeholder="예: 2026-08 월간 보고"
                    className={REPORT_DRAWER_STYLE.control}
                  />
                </label>
                <button
                  type="button"
                  onClick={() => {
                    if (!report.doctor_artifact_sha256) return
                    onMarkSent({
                      artifact_sha256: report.doctor_artifact_sha256,
                      recipient_label: recipientLabel.trim(),
                      channel: channel.trim(),
                      note: deliveryNote.trim() || undefined,
                    })
                  }}
                  disabled={markingSent || !canSubmitDelivery}
                  className={`${REPORT_DRAWER_STYLE.success} block min-h-11 w-full px-4 py-3 text-center text-sm font-medium hover:opacity-80 disabled:opacity-50`}
                >
                  {markingSent
                    ? '처리 중...'
                    : deliveryReady
                      ? '원장 전달 기록 남기기'
                      : '전달 전 차단 항목을 해결해 주세요'}
                </button>
              </div>
            )}
            {(report.delivery_history?.length ?? 0) > 0 && (
              <div className="mt-4 border-t border-[var(--color-revisit-coolgrey-20)] pt-4">
                <h5 className="text-sm font-semibold text-[var(--color-revisit-text-title)]">전달 이력</h5>
                <ol className="mt-2 space-y-2">
                  {report.delivery_history?.map((event, index) => (
                    <li key={asString(event.id) ?? index} className="rounded-md bg-[var(--color-revisit-coolgrey-90)] px-3 py-2 text-xs leading-5 text-[var(--color-revisit-text-helper)]">
                      <div className="font-semibold text-[var(--color-revisit-text-title)]">
                        {asString(event.event_type) ?? '전달 이벤트'} · {formatDate(asString(event.created_at))}
                      </div>
                      <div>
                        수신 {asString(event.recipient_label) ?? '-'} · 채널 {asString(event.channel) ?? '-'} · 담당 {asString(event.operator) ?? '-'}
                      </div>
                      {asString(event.artifact_sha256) && (
                        <div className={`break-all font-mono text-[11px] ${REPORT_DRAWER_STYLE.helper}`}>
                          SHA-256 {asString(event.artifact_sha256)}
                        </div>
                      )}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}

function EssenceRow({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone: 'ok' | 'warn'
}) {
  const cls = tone === 'warn'
    ? 'font-medium text-[var(--color-revisit-nav)]'
    : 'font-medium text-[var(--color-revisit-text-title)]'
  return (
    <div className="flex justify-between gap-2">
      <span className={REPORT_DRAWER_STYLE.helper}>{label}</span>
      <span className={cls}>{value}</span>
    </div>
  )
}
