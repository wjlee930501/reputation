'use client'

import { useState } from 'react'
import { isValidReportRebuildReason } from '@/lib/report-run'
import { reportRebuildPlan } from '@/lib/report-rebuild'
import type { ReportView } from '@/lib/report-review'
import { isEffectivelyDelivered } from '@/lib/report-delivery'

/**
 * 보고서 목록 한 행의 '다시 만들기'.
 *
 * 작업 카드(ReportRunCard)와 같은 사유 입력·같은 계약을 쓰되, 그쪽은 최근 3건만 보여 주므로
 * 과거 월은 여기서만 손이 닿는다. 사유를 받는 이유도 같다 — 새 버전이 왜 생겼는지가
 * 감사 기록에 남지 않으면 원장 전달본이 둘일 때 어느 쪽이 맞는지 아무도 답할 수 없다.
 */
export function ReportRebuildButton({
  report,
  disabled,
  onRebuild,
}: {
  report: ReportView
  disabled: boolean
  onRebuild: (reason: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const plan = reportRebuildPlan({
    periodYear: report.periodYear,
    periodMonth: report.periodMonth,
    deliveryTracked: report.deliveryTracked,
    delivered: isEffectivelyDelivered(report),
  })

  if (plan.kind === 'unavailable') {
    return (
      <p className="mt-2 max-w-xs text-xs leading-5 text-[var(--color-revisit-text-caption)] [word-break:keep-all]">
        {plan.reason}
      </p>
    )
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        disabled={disabled}
        className="mt-2 min-h-11 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white px-4 text-xs font-bold text-[var(--color-revisit-text-helper)] disabled:opacity-50 sm:w-auto"
      >
        다시 만들기
      </button>
    )
  }

  const fieldId = `rebuild-reason-${report.id}`
  return (
    <div className="mt-2 rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-left">
      <label htmlFor={fieldId} className="text-sm font-bold text-[var(--color-revisit-text-title)]">
        새 버전을 만드는 이유
      </label>
      <p className="mt-1 text-xs leading-5 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">
        기존 보고서는 지우지 않고 보존합니다. 반영할 내용을 3자 이상 적어 주세요.
      </p>
      {plan.warning && (
        <p className="mt-2 text-xs leading-5 text-[var(--color-revisit-red-50)] [word-break:keep-all]" role="alert">
          {plan.warning}
        </p>
      )}
      <textarea
        id={fieldId}
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        maxLength={200}
        rows={2}
        className="mt-2 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white p-3 text-sm"
      />
      <div className="mt-2 flex flex-col gap-2 sm:flex-row">
        <button
          type="button"
          onClick={() => onRebuild(reason.trim())}
          disabled={disabled || !isValidReportRebuildReason(reason)}
          className="min-h-11 rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white disabled:opacity-50"
        >
          이 사유로 새 버전 만들기
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white px-4 text-sm font-bold text-[var(--color-revisit-text-helper)]"
        >
          취소
        </button>
      </div>
    </div>
  )
}
