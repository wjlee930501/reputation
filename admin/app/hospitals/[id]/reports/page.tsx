'use client'

import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import { fetchAPI } from '@/lib/api'

interface Report {
  id: string
  hospital_id: string
  period_year: number
  period_month: number
  report_type: string
  has_pdf: boolean
  download_url: string | null
  created_at: string
  sent_at: string | null
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
  PDF_PENDING: { label: 'PDF 생성 중', cls: 'bg-amber-100 text-amber-700' },
  AWAITING_REVIEW: { label: '검수 대기', cls: 'bg-blue-100 text-blue-700' },
  DELIVERED: { label: '전달 완료', cls: 'bg-green-100 text-green-700' },
}

function getScreeningStatus(r: Report): ScreeningStatus {
  if (r.sent_at) return 'DELIVERED'
  if (!r.download_url && !r.has_pdf) return 'PDF_PENDING'
  return 'AWAITING_REVIEW'
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
  return JSON.stringify(value)
}

function SummaryGrid({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(([, v]) => v !== null && v !== undefined)
  if (entries.length === 0) {
    return <p className="text-sm text-gray-400">표시할 항목이 없습니다.</p>
  }
  return (
    <div className="grid grid-cols-2 gap-x-4 gap-y-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-2 text-sm">
          <span className="text-gray-600">{humanizeKey(k)}</span>
          {isPlainObject(v) || Array.isArray(v) ? (
            <pre className="text-xs bg-white border border-gray-200 rounded px-2 py-1 max-w-[60%] overflow-x-auto">
              {JSON.stringify(v, null, 2)}
            </pre>
          ) : (
            <span className="font-medium text-gray-900">{renderSummaryValue(k, v)}</span>
          )}
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
          ok ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
        }`}
      >
        {ok ? '✓' : '!'}
      </span>
      <div className="flex-1">
        <div className={ok ? 'text-gray-800' : 'text-amber-800 font-medium'}>{label}</div>
        {hint && <div className="text-xs text-gray-500 mt-0.5">{hint}</div>}
      </div>
    </div>
  )
}

export default function ReportsPage() {
  const { id } = useParams<{ id: string }>()
  const [reports, setReports] = useState<Report[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Report | null>(null)
  const [detailLoadingId, setDetailLoadingId] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)

  useEffect(() => {
    fetchAPI(`/admin/hospitals/${id}/reports`)
      .then(setReports)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  async function openDetail(report: Report) {
    setDetailError(null)
    setDetailLoadingId(report.id)
    try {
      const full = await fetchAPI(`/admin/hospitals/${id}/reports/${report.id}`)
      setSelected(full)
    } catch (e: unknown) {
      setDetailError(e instanceof Error ? e.message : '리포트 상세 정보를 불러오지 못했습니다.')
      setSelected(null)
    } finally {
      setDetailLoadingId(null)
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
    <div className="p-8">
      <div className="mb-6">
        <h2 className="text-xl font-bold text-gray-900">리포트 스크리닝</h2>
        <p className="mt-1 text-sm text-gray-600">
          원장님께 전달하기 전 AI 답변 노출, 콘텐츠 성과, 운영 기준 정합성을 확인합니다.
        </p>
      </div>

      {!loading && !error && (
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          <SummaryCard label="검수 대기" value={stats.awaiting} tone="blue" />
          <SummaryCard label="PDF 준비" value={stats.pdfReady} tone="indigo" />
          <SummaryCard label="전달 완료" value={stats.delivered} tone="green" />
          <SummaryCard label="이번 달 리포트" value={stats.thisMonth} tone="gray" />
        </div>
      )}

      {loading && <div className="text-center py-16 text-gray-500">불러오는 중...</div>}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">오류: {error}</div>
      )}

      {detailError && (
        <div className="mb-4 bg-amber-50 border border-amber-200 rounded-lg p-4 text-amber-800 text-sm">
          리포트 상세를 불러오지 못했습니다. 원장 보고 전 스크리닝 데이터가 불완전할 수 있습니다. ({detailError})
        </div>
      )}

      {!loading && !error && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">기간</th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">리포트 유형</th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">스크리닝 상태</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">PDF</th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">생성일</th>
                <th className="text-right px-6 py-3 text-gray-600 font-medium">액션</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {reports.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-center py-12 text-gray-400">
                    아직 생성된 리포트가 없습니다. 월간 리포트가 생성되면 여기에서 검수 후 원장님께 전달합니다.
                  </td>
                </tr>
              )}
              {reports.map((r) => {
                const status = getScreeningStatus(r)
                const meta = SCREENING_LABELS[status]
                return (
                  <tr key={r.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4 text-gray-900 font-medium">
                      {r.period_year}년 {r.period_month}월
                    </td>
                    <td className="px-6 py-4 text-gray-600">
                      {TYPE_LABELS[r.report_type] ?? r.report_type}
                    </td>
                    <td className="px-6 py-4">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${meta.cls}`}>
                        {meta.label}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      {r.download_url ? (
                        <a
                          href={r.download_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="px-3 py-1 bg-blue-100 text-blue-700 text-xs rounded hover:bg-blue-200"
                        >
                          다운로드
                        </a>
                      ) : r.has_pdf ? (
                        <span className="text-blue-600 text-xs">링크 준비 중</span>
                      ) : (
                        <span className="text-gray-400 text-xs">생성 중</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-gray-600">
                      <div>{formatDate(r.created_at)}</div>
                      {r.sent_at && <div className="text-xs text-green-700 mt-0.5">전달 {formatDate(r.sent_at)}</div>}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <button
                        onClick={() => openDetail(r)}
                        disabled={detailLoadingId === r.id}
                        className="px-3 py-1 bg-gray-900 text-white text-xs rounded hover:bg-gray-700 disabled:opacity-60"
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
        <DetailDrawer report={selected} onClose={() => setSelected(null)} />
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
    gray: 'border-gray-200 bg-gray-50 text-gray-900',
  }
  return (
    <div className={`rounded-xl border p-4 ${toneCls[tone]}`}>
      <div className="text-xs font-medium opacity-80">{label}</div>
      <div className="mt-1 text-2xl font-bold">{value}</div>
    </div>
  )
}

function DetailDrawer({ report, onClose }: { report: Report; onClose: () => void }) {
  const status = getScreeningStatus(report)
  const meta = SCREENING_LABELS[status]
  const sov = isPlainObject(report.sov_summary) ? report.sov_summary : null
  const content = isPlainObject(report.content_summary) ? report.content_summary : null
  const essence = isPlainObject(report.essence_summary) ? report.essence_summary : null

  const recommendedActions = essence && Array.isArray(essence.recommended_actions)
    ? (essence.recommended_actions as unknown[]).map((v) => String(v))
    : []
  const medicalRiskFindings = essence && Array.isArray(essence.medical_risk_findings)
    ? (essence.medical_risk_findings as Array<Record<string, unknown>>)
    : []

  return (
    <div className="fixed inset-0 bg-black/50 flex items-start justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full my-8">
        <div className="flex items-center justify-between p-6 border-b border-gray-200">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-lg font-bold text-gray-900">
                {TYPE_LABELS[report.report_type] ?? report.report_type} — {report.period_year}년 {report.period_month}월
              </h3>
              <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${meta.cls}`}>
                {meta.label}
              </span>
              {report.download_url && (
                <a
                  href={report.download_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-600 text-white hover:bg-blue-700"
                >
                  PDF 다운로드
                </a>
              )}
            </div>
            <div className="mt-1 text-xs text-gray-500">
              생성 {formatDate(report.created_at)}
              {report.sent_at ? ` · 전달 ${formatDate(report.sent_at)}` : ''}
            </div>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl" aria-label="닫기">
            ✕
          </button>
        </div>

        <div className="p-6 space-y-6">
          <section className="rounded-lg border border-gray-200 p-4">
            <h4 className="text-sm font-semibold text-gray-900 mb-3">원장 보고 전 체크</h4>
            <div className="space-y-2">
              <ChecklistRow
                ok={Boolean(report.download_url || report.has_pdf)}
                label="PDF 준비 완료"
                hint={report.download_url ? undefined : '생성이 완료되면 다운로드 버튼이 활성화됩니다.'}
              />
              <ChecklistRow ok={Boolean(sov)} label="AI 답변 언급률 요약 존재" />
              <ChecklistRow ok={Boolean(content)} label="콘텐츠 성과 요약 존재" />
              <ChecklistRow ok={Boolean(essence)} label="운영 기준 요약 존재" />
              {recommendedActions.length > 0 && (
                <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3">
                  <div className="text-xs font-semibold text-amber-800 mb-1">권장 조치</div>
                  <ul className="list-disc list-inside text-sm text-amber-900 space-y-0.5">
                    {recommendedActions.map((action, i) => (
                      <li key={i}>{action}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </section>

          <section>
            <h4 className="text-sm font-semibold text-gray-900 mb-3">이번 달 핵심 변화</h4>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-lg bg-blue-50 border border-blue-100 p-4">
                <p className="text-xs font-semibold text-blue-700 mb-2">AI 답변 언급률</p>
                {sov ? <SummaryGrid data={sov} /> : <p className="text-sm text-gray-400">데이터 없음</p>}
              </div>
              <div className="rounded-lg bg-gray-50 border border-gray-200 p-4">
                <p className="text-xs font-semibold text-gray-700 mb-2">콘텐츠 성과</p>
                {content ? <SummaryGrid data={content} /> : <p className="text-sm text-gray-400">데이터 없음</p>}
              </div>
            </div>
          </section>

          {essence && (
            <section>
              <h4 className="text-sm font-semibold text-gray-900 mb-3">콘텐츠 운영 기준</h4>
              <div className="rounded-lg border border-gray-200 p-4 space-y-3">
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
                  <div className="rounded-md border border-red-200 bg-red-50 p-3">
                    <div className="text-xs font-semibold text-red-800 mb-1">
                      의료광고 리스크 {medicalRiskFindings.length}건
                    </div>
                    <ul className="text-sm text-red-900 space-y-1">
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
                        <li className="text-xs text-red-800">외 {medicalRiskFindings.length - 5}건</li>
                      )}
                    </ul>
                  </div>
                )}
              </div>
            </section>
          )}

          <section>
            <h4 className="text-sm font-semibold text-gray-900 mb-3">원장 보고 자료</h4>
            {report.download_url ? (
              <a
                href={report.download_url}
                target="_blank"
                rel="noopener noreferrer"
                className="block w-full text-center py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700"
              >
                PDF 다운로드
              </a>
            ) : (
              <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 py-4 text-center text-sm text-gray-500">
                {report.has_pdf ? 'PDF 링크 준비 중' : 'PDF 생성 중 — 잠시 후 다시 확인해 주세요.'}
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
  const cls = tone === 'warn' ? 'text-amber-800 font-medium' : 'text-gray-900 font-medium'
  return (
    <div className="flex justify-between gap-2">
      <span className="text-gray-600">{label}</span>
      <span className={cls}>{value}</span>
    </div>
  )
}
