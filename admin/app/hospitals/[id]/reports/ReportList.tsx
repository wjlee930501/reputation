'use client'

import {
  reportStatusLabel,
  reportSummaryCounts,
  shouldShowDeliveryProblem,
} from '@/lib/report-delivery'
import type { ReportView } from '@/lib/report-review'

function formatDate(value: string | null): string {
  if (!value) return '-'
  return new Date(value).toLocaleDateString('ko-KR')
}

export function ReportList({
  reports,
  loadingId,
  onOpen,
}: {
  reports: readonly ReportView[]
  loadingId: string | null
  onOpen: (report: ReportView) => void
}) {
  const { delivered, ready, blocked } = reportSummaryCounts(reports)
  return (
    <section data-current-task aria-labelledby="report-list-heading">
      <div className="mb-4 grid grid-cols-3 gap-2 sm:gap-3">
        <Summary label="전달 전 검수 가능" value={ready} />
        <Summary label="조치 필요" value={blocked} />
        <Summary label="전달 기록 있음" value={delivered} />
      </div>
      <h3 id="report-list-heading" className="sr-only">보고서 목록</h3>
      {reports.length === 0 ? (
        <div className="rounded-xl border border-dashed border-[var(--color-revisit-coolgrey-20)] bg-white px-5 py-8 text-center">
          <p className="font-semibold text-[var(--color-revisit-text-title)]">아직 검수할 보고서가 없습니다.</p>
          <p className="mt-2 text-sm leading-6 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">
            위에서 대상 월을 선택해 보고서를 만들거나, 운영 센터에서 측정 차단 사유를 확인해 주세요.
          </p>
        </div>
      ) : (
        <div className="admin-responsive-table-wrap overflow-hidden rounded-xl border border-[var(--color-revisit-coolgrey-20)] bg-white">
          <table className="admin-responsive-table w-full text-sm">
            <thead className="border-b border-[var(--color-revisit-coolgrey-20)] bg-[var(--color-revisit-coolgrey-90)]">
              <tr>
                <th className="px-5 py-3 text-left font-semibold">기간</th>
                <th className="px-5 py-3 text-left font-semibold">현재 상태</th>
                <th className="px-5 py-3 text-left font-semibold">생성일</th>
                <th className="px-5 py-3 text-right font-semibold">다음 행동</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[var(--color-revisit-coolgrey-20)]">
              {/* 초기 진단(V0)과 월간이 한 줄씩, 행동은 ‘열기’ 하나뿐이다. */}
              {reports.map((report) => (
                <tr key={report.id} className="align-top">
                  <td className="px-5 py-4 font-bold text-[var(--color-revisit-text-title)]" data-primary="true">
                    {report.periodYear}년 {report.periodMonth}월
                    <span className="mt-1 block text-xs font-normal text-[var(--color-revisit-text-caption)]">{report.typeLabel}</span>
                  </td>
                  <td className="px-5 py-4" data-label="현재 상태">
                    <span className="font-semibold text-[var(--color-revisit-text-title)]">
                      {reportStatusLabel(report)}
                    </span>
                    {shouldShowDeliveryProblem(report) && (
                      <p className="mt-1 max-w-sm text-xs leading-5 text-[var(--color-revisit-red-50)] [word-break:keep-all]">
                        문제: {report.deliveryBlockers[0] ?? '최신 전달 가능 상태를 확인할 수 없습니다.'}<br />
                        고객 영향: {report.deliveryTracked
                          ? '최신 월간 보고 자료를 원장에게 전달할 수 없습니다.'
                          : '초기 진단 결과를 원장에게 보고할 자료가 없습니다.'}<br />
                        지금 할 일: ‘열기’에서 해결 방법을 확인하세요.
                      </p>
                    )}
                    {!report.deliveryTracked && report.deliveryReady && (
                      <p className="mt-1 max-w-sm text-xs leading-5 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">
                        초기 진단은 전달 기록을 남기지 않습니다. ‘열기’에서 근거를 확인한 뒤 원장에게 직접 보고해 주세요.
                      </p>
                    )}
                    {report.deliveryWarnings.length > 0 && (
                      <p className="mt-1 max-w-sm text-xs leading-5 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">
                        주의: {report.deliveryWarnings[0]}
                      </p>
                    )}
                  </td>
                  <td className="px-5 py-4 text-[var(--color-revisit-text-helper)]" data-label="생성일">
                    {formatDate(report.createdAt)}
                  </td>
                  <td className="px-5 py-4 text-right" data-label="다음 행동">
                    <button type="button" onClick={() => onOpen(report)} disabled={loadingId === report.id} className="min-h-11 rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-xs font-bold text-white disabled:opacity-50">
                      {loadingId === report.id ? '여는 중' : '열기'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Summary({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] bg-white p-3 sm:p-4">
      <p className="text-xs text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{label}</p>
      <p className="mt-1 text-2xl font-bold text-[var(--color-revisit-text-title)]">{value}</p>
    </div>
  )
}
