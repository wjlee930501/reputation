'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { DeliveryIssue } from '@/lib/report-delivery'
import type { ReportView } from '@/lib/report-review'
import { dialogKeyDecision } from '@/lib/report-component-behavior'
import { ReportDelivery, type DeliveryAction } from './ReportDelivery'
import { ReportEvidence } from './ReportEvidence'

export function ReportReviewDialog({
  report,
  issue,
  isOwner,
  busy,
  onClose,
  onRefresh,
  onAction,
  onCopyIssue,
  onCopyNotification,
}: {
  report: ReportView
  issue: DeliveryIssue | null
  isOwner: boolean
  busy: boolean
  onClose: () => void
  onRefresh: () => void
  onAction: (action: DeliveryAction) => void
  onCopyIssue: () => void
  onCopyNotification: () => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const issueRef = useRef<HTMLDivElement>(null)
  const [mounted, setMounted] = useState(false)

  useEffect(() => setMounted(true), [])

  useEffect(() => {
    if (!mounted) return
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const main = document.querySelector<HTMLElement>('#main-content')
    const oldOverflow = document.body.style.overflow
    main?.setAttribute('inert', '')
    main?.setAttribute('aria-hidden', 'true')
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()
    function onKeyDown(event: KeyboardEvent) {
      if (!panelRef.current) return
      const focusable = Array.from(panelRef.current.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'))
      const first = focusable[0]
      const last = focusable.at(-1)
      const decision = dialogKeyDecision(
        event.key,
        event.shiftKey,
        document.activeElement === first,
        document.activeElement === last,
      )
      if (decision === 'close') { event.preventDefault(); onClose(); return }
      if (!focusable.length || decision === 'native') return
      event.preventDefault()
      if (decision === 'first') first.focus()
      else last?.focus()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      main?.removeAttribute('inert')
      main?.removeAttribute('aria-hidden')
      document.body.style.overflow = oldOverflow
      previous?.focus()
    }
  }, [mounted, onClose])

  useEffect(() => {
    if (issue) window.requestAnimationFrame(() => issueRef.current?.focus())
  }, [issue])

  if (!mounted) return null
  return createPortal(
    <div className="fixed inset-0 z-50 overflow-y-auto bg-[color-mix(in_srgb,var(--color-revisit-nav)_60%,transparent)] p-2 sm:p-5" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby="report-dialog-title" aria-describedby="report-dialog-description" className="mx-auto my-2 w-full max-w-4xl rounded-xl bg-white sm:my-5">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-3 rounded-t-xl border-b border-[var(--color-revisit-coolgrey-20)] bg-white p-4 sm:p-5">
          <div>
            <p className="text-xs font-bold text-[var(--color-revisit-primary-40)]">{report.statusLabel}</p>
            <h3 id="report-dialog-title" className="mt-1 text-lg font-bold text-[var(--color-revisit-text-title)] [word-break:keep-all]">{report.periodYear}년 {report.periodMonth}월 {report.typeLabel}</h3>
            <p id="report-dialog-description" className="mt-1 text-sm text-[var(--color-revisit-text-helper)]">근거를 먼저 확인한 뒤 맨 아래에서 원장 전달 기록을 남깁니다.</p>
          </div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="리포트 검수 닫기" className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-[var(--color-revisit-coolgrey-20)]">
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div className="space-y-5 p-3 sm:p-5">
          {issue && (
            <div ref={issueRef} tabIndex={-1} role="alert" aria-labelledby="delivery-issue-title" className="rounded-xl border-2 border-[var(--color-revisit-red-50)] bg-white p-4 outline-none focus:ring-2 focus:ring-[var(--color-revisit-primary-40)]">
              <h4 id="delivery-issue-title" className="text-lg font-bold text-[var(--color-revisit-red-50)]">{issue.title}</h4>
              <dl className="mt-3 grid gap-3 text-sm leading-6 md:grid-cols-3"><IssueItem label="무슨 문제인지" value={issue.problem} /><IssueItem label="고객 영향" value={issue.customerImpact} /><IssueItem label="지금 할 일" value={issue.nextAction} /></dl>
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <button type="button" onClick={onRefresh} className="min-h-11 rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white">최신 상태 다시 확인</button>
                {issue.action === 'operations' && <Link href={`/operations?queue=REPORTS&hospital_id=${report.hospitalId}`} className="inline-flex min-h-11 items-center justify-center rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">운영 센터에서 차단 사유 확인</Link>}
                <button type="button" onClick={onCopyIssue} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">개발팀 문의용 정보 복사</button>
              </div>
            </div>
          )}
          <ReportEvidence report={report} onCopyNotification={onCopyNotification} />
          <ReportDelivery report={report} isOwner={isOwner} busy={busy} onAction={onAction} />
        </div>
      </div>
    </div>,
    document.body,
  )
}

function IssueItem({ label, value }: { label: string; value: string }) {
  return <div><dt className="font-bold text-[var(--color-revisit-text-title)]">{label}</dt><dd className="mt-1 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{value}</dd></div>
}
