'use client'

import Link from 'next/link'
import { useState } from 'react'
import { isValidReportRebuildReason, type ReportRunView } from '@/lib/report-run'

export function ReportRunCard({
  run,
  disabled,
  operationsHref,
  onReview,
  onRebuild,
  onCopy,
}: {
  run: ReportRunView
  disabled: boolean
  operationsHref: string
  onReview: () => void
  onRebuild: (reason: string) => void
  onCopy: () => void
}) {
  const [reasonOpen, setReasonOpen] = useState(false)
  const [reason, setReason] = useState('')
  return (
    <article className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h4 className="font-bold text-[var(--color-revisit-text-title)] [word-break:keep-all]">
            {run.periodYear}년 {run.periodMonth}월 · {run.statusLabel}
          </h4>
          {run.versionLabel && <p className="mt-1 text-xs text-[var(--color-revisit-text-caption)]">{run.versionLabel}</p>}
        </div>
        <span className="rounded-full bg-[var(--color-revisit-primary-95)] px-3 py-1 text-xs font-semibold text-[var(--color-revisit-nav)]">{run.attentionLabel}</span>
      </div>
      <dl className="mt-4 grid gap-3 text-sm leading-6 md:grid-cols-3">
        <CopyItem label="무슨 문제인지" value={run.whatHappened} />
        <CopyItem label="고객 영향" value={run.customerImpact} />
        <CopyItem label="지금 할 일" value={run.nextAction} />
      </dl>
      {!run.isActive && (
        <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          {run.primaryAction === 'review' && <Action onClick={onReview} disabled={disabled} primary>원장 전달 자료 확인</Action>}
          {run.primaryAction === 'operations' && (
            <Link href={operationsHref} className="inline-flex min-h-11 items-center justify-center rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white">운영 센터에서 차단 사유 확인</Link>
          )}
          {run.canRebuild && !reasonOpen && <Action onClick={() => setReasonOpen(true)} disabled={disabled} primary={run.primaryAction === 'rebuild'}>리포트 다시 만들기</Action>}
          <Action onClick={onCopy}>개발팀 문의용 정보 복사</Action>
        </div>
      )}
      {reasonOpen && (
        <div className="mt-4 rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3">
          <label htmlFor={`reason-${run.runId}`} className="text-sm font-bold text-[var(--color-revisit-text-title)]">새 버전을 만드는 이유</label>
          <p className="mt-1 text-xs leading-5 text-[var(--color-revisit-text-helper)]">기존 리포트는 지우지 않고 보존합니다. 반영할 내용을 3자 이상 적어 주세요.</p>
          <textarea id={`reason-${run.runId}`} value={reason} onChange={(event) => setReason(event.target.value)} maxLength={200} rows={2} className="mt-2 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white p-3 text-sm" />
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <Action onClick={() => onRebuild(reason.trim())} disabled={disabled || !isValidReportRebuildReason(reason)} primary>이 사유로 새 버전 만들기</Action>
            <Action onClick={() => setReasonOpen(false)}>취소</Action>
          </div>
        </div>
      )}
    </article>
  )
}

function CopyItem({ label, value }: { label: string; value: string }) {
  return <div><dt className="font-bold text-[var(--color-revisit-text-title)]">{label}</dt><dd className="mt-1 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{value}</dd></div>
}

function Action({ children, onClick, disabled = false, primary = false }: { children: React.ReactNode; onClick: () => void; disabled?: boolean; primary?: boolean }) {
  return <button type="button" onClick={onClick} disabled={disabled} className={`min-h-11 rounded-lg px-4 text-sm font-bold disabled:opacity-50 ${primary ? 'bg-[var(--color-revisit-primary-40)] text-white' : 'border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-helper)]'}`}>{children}</button>
}
