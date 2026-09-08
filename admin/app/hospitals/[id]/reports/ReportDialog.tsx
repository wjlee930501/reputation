'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  deliveryEventLabel,
  getDoctorDownload,
  getInternalReportLabel,
  isEffectivelyDelivered,
  latestDeliveryEvent,
  sha256OfDownloadedPdf,
  type DeliveryIssue,
} from '@/lib/report-delivery'
import type { ReportView } from '@/lib/report-review'
import { dialogKeyDecision } from '@/lib/report-component-behavior'
import { reportOperationsHref } from '@/lib/operations-center'
import { ReportEvidence } from './ReportEvidence'

export type DeliveryAction =
  | { kind: 'deliver'; artifactSha256: string; recipient: string; channel: string; note?: string }
  | { kind: 'correct'; artifactSha256: string; recipient: string; channel: string; note?: string; reason: string }
  | { kind: 'rescind'; reason: string }

const DOWNLOAD_REQUIRED = '먼저 원장 전달용 파일을 여기서 내려받아야 합니다'

function format(value: string | null): string {
  return value ? new Date(value).toLocaleString('ko-KR') : '-'
}

/**
 * 보고서 하나를 여는 유일한 다이얼로그. 순서는 열기 → 전달 기록 → 이력이며,
 * 초기 진단(V0)과 월간이 같은 화면을 쓴다. 내부 검수 근거는 원장 대면 절차를 가리지
 * 않도록 맨 아래 ‘내부 상태’ 안에 접어 둔다.
 */
export function ReportDialog({
  report,
  issue,
  isOwner,
  busy,
  downloadedSha256,
  onDownloaded,
  onClose,
  onRefresh,
  onAction,
  onCopyDeveloperInfo,
}: {
  report: ReportView
  issue: DeliveryIssue | null
  isOwner: boolean
  busy: boolean
  /** 이 화면이 실제로 내려받은 파일의 확인 번호. 전달 기록은 이 값에만 결합한다. */
  downloadedSha256: string | null
  onDownloaded: (sha256: string) => void
  onClose: () => void
  onRefresh: () => void
  onAction: (action: DeliveryAction) => void
  onCopyDeveloperInfo: () => void
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const issueRef = useRef<HTMLDivElement>(null)
  const [mounted, setMounted] = useState(false)

  const delivered = isEffectivelyDelivered(report)
  const doctorUrl = getDoctorDownload(report.hospitalId, report.id, report.doctorArtifact.state, report)
  const current = latestDeliveryEvent(report.deliveryHistory)
  const [recipient, setRecipient] = useState(current?.recipient === '-' ? '' : current?.recipient ?? '')
  const [channel, setChannel] = useState(current?.channel === '-' ? '대면' : current?.channel ?? '대면')
  const [note, setNote] = useState('')
  const [reason, setReason] = useState('')
  const [mode, setMode] = useState<'none' | 'correct' | 'rescind'>('none')
  const [downloading, setDownloading] = useState(false)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const deliveryValid = recipient.trim().length > 0 && channel.trim().length > 0
  const reasonValid = reason.trim().length >= 2

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

  function openDoctorReport() {
    if (!doctorUrl || downloading) return
    // 팝업 차단을 피하려면 클릭과 같은 순간에 탭을 열어야 한다. 내려받기가 끝난 뒤 주소를 넣는다.
    const tab = window.open('', '_blank')
    setDownloading(true)
    setDownloadError(null)
    sha256OfDownloadedPdf(doctorUrl, (blob) => {
      const objectUrl = URL.createObjectURL(blob)
      if (tab) tab.location.href = objectUrl
      else {
        // 탭이 막힌 브라우저에서는 같은 바이트를 파일로 내려준다.
        const link = document.createElement('a')
        link.href = objectUrl
        link.download = `${report.periodYear}-${String(report.periodMonth).padStart(2, '0')}-doctor-report.pdf`
        link.click()
      }
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
    })
      .then(onDownloaded)
      .catch(() => {
        tab?.close()
        setDownloadError('원장 전달용 파일을 내려받지 못했습니다. 잠시 뒤 다시 눌러 주세요.')
      })
      .finally(() => setDownloading(false))
  }

  if (!mounted) return null
  return createPortal(
    <div className="fixed inset-0 z-50 overflow-y-auto bg-[color-mix(in_srgb,var(--color-revisit-nav)_60%,transparent)] p-2 sm:p-5" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby="report-dialog-title" aria-describedby="report-dialog-description" className="mx-auto my-2 w-full max-w-4xl rounded-xl bg-white sm:my-5">
        <header className="sticky top-0 z-10 flex items-start justify-between gap-3 rounded-t-xl border-b border-[var(--color-revisit-coolgrey-20)] bg-white p-4 sm:p-5">
          <div>
            <p className="text-xs font-bold text-[var(--color-revisit-primary-40)]">{report.statusLabel}</p>
            <h3 id="report-dialog-title" className="mt-1 text-lg font-bold text-[var(--color-revisit-text-title)] [word-break:keep-all]">{report.periodYear}년 {report.periodMonth}월 {report.typeLabel}</h3>
            <p id="report-dialog-description" className="mt-1 text-sm text-[var(--color-revisit-text-helper)]">원장 전달용 파일을 열고, 전달 기록을 남기고, 이력을 확인합니다.</p>
          </div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="보고서 닫기" className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg border border-[var(--color-revisit-coolgrey-20)]">
            <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div className="space-y-5 p-3 sm:p-5">
          {issue && (
            <div ref={issueRef} tabIndex={-1} role="alert" aria-labelledby="delivery-issue-title" className="rounded-xl border-2 border-[var(--color-revisit-red-50)] bg-white p-4 outline-none focus:ring-2 focus:ring-[var(--color-revisit-primary-40)]">
              <h4 id="delivery-issue-title" className="text-lg font-bold text-[var(--color-revisit-red-50)]">{issue.title}</h4>
              <dl className="mt-3 grid gap-3 text-sm leading-6 md:grid-cols-3"><Copy label="무슨 문제인지" value={issue.problem} /><Copy label="고객 영향" value={issue.customerImpact} /><Copy label="지금 할 일" value={issue.nextAction} /></dl>
              <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                <button type="button" onClick={onRefresh} className="min-h-11 rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white">최신 상태 다시 확인</button>
                {issue.action === 'operations' && <Link href={reportOperationsHref(report.hospitalId, report.id, report.periodYear, report.periodMonth)} className="inline-flex min-h-11 items-center justify-center rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">운영 센터에서 차단 사유 확인</Link>}
                <button type="button" onClick={onCopyDeveloperInfo} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">개발팀 문의용 정보 복사</button>
              </div>
            </div>
          )}

          <section data-report-section="open" className="rounded-xl border-2 border-[var(--color-revisit-primary-40)] p-4" aria-labelledby="report-open-heading">
            <h4 id="report-open-heading" className="text-lg font-bold text-[var(--color-revisit-text-title)]">1. 원장용 파일 열기</h4>
            <p className="mt-2 text-sm leading-6 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">
              여기서 연 파일이 원장님께 드릴 파일입니다. 전달 기록은 이 파일에만 결합합니다.
            </p>
            <div className="mt-4 grid gap-2">
              {doctorUrl ? (
                <button type="button" onClick={openDoctorReport} disabled={downloading} className="inline-flex min-h-11 items-center justify-center rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white disabled:opacity-40">{downloading ? '원장 전달용 보고서를 내려받는 중' : '원장 전달용 보고서 열기'}</button>
              ) : (
                <div className="rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-sm leading-6">
                  <strong>문제:</strong> 검증된 원장 전달용 보고서를 열 수 없습니다.<br />
                  <strong>고객 영향:</strong> 내부 검수용 파일로 대신 전달할 수 없습니다.<br />
                  <strong>지금 할 일:</strong> 아래 ‘내부 상태’에서 차단 항목을 확인해 해결해 주세요.
                </div>
              )}
              {downloadError && <p className="text-sm text-[var(--color-revisit-red-50)]" role="alert">{downloadError}</p>}
              {downloadedSha256 && <p className="text-sm text-[var(--color-revisit-text-helper)]">내려받은 파일 확인 번호 · 앞 12자리 {downloadedSha256.slice(0, 12)}</p>}
            </div>
          </section>

          <section data-report-section="delivery" className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] p-4" aria-labelledby="report-delivery-heading">
            <h4 id="report-delivery-heading" className="text-lg font-bold text-[var(--color-revisit-text-title)]">2. 전달 기록</h4>
            {delivered ? (
              <div className="mt-3 rounded-lg border border-[var(--color-revisit-green-50)] p-3 text-sm leading-6">
                <strong>현재 유효한 전달 기록</strong>
                <p className="mt-1">{current ? deliveryEventLabel(current.type) : '전달 기록'} · {format(current?.createdAt ?? report.sentAt)}</p>
                <p>받은 분 {current?.recipient ?? '-'} · 전달 방법 {current?.channel ?? '-'}</p>
                <p className="mt-1 text-xs text-[var(--color-revisit-text-helper)]">전달 당시 검증본 확인 번호 · 앞 12자리 {report.doctorArtifact.sha256?.slice(0, 12) ?? '확인 불가'} · {report.review?.versionLabel ?? '버전 확인 불가'}</p>
              </div>
            ) : (
              <>
                <DeliveryFields recipient={recipient} channel={channel} note={note} onRecipient={setRecipient} onChannel={setChannel} onNote={setNote} />
                <button type="button" disabled={busy || !doctorUrl || !deliveryValid || !downloadedSha256} onClick={() => downloadedSha256 && onAction({ kind: 'deliver', artifactSha256: downloadedSha256, recipient: recipient.trim(), channel: channel.trim(), note: note.trim() || undefined })} className="mt-3 min-h-11 w-full rounded-lg bg-[var(--color-revisit-green-50)] px-4 text-sm font-bold text-white disabled:opacity-40">
                  {busy ? '최신 상태 확인 중' : !report.deliveryReady ? '차단 항목을 해결한 뒤 기록할 수 있습니다' : !downloadedSha256 ? DOWNLOAD_REQUIRED : '이 파일의 원장 전달 기록 남기기'}
                </button>
              </>
            )}
          </section>

          <section data-report-section="history" className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] p-4" aria-labelledby="report-history-heading">
            <h4 id="report-history-heading" className="text-lg font-bold text-[var(--color-revisit-text-title)]">3. 전달 이력</h4>
            {report.deliveryHistory.length > 0 ? (
              <ol className="mt-3 grid gap-2">
                {report.deliveryHistory.map((event) => <li key={event.id} className="rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-sm leading-5"><strong>{deliveryEventLabel(event.type)}</strong> · {format(event.createdAt)}<br />받은 분 {event.recipient} · 전달 방법 {event.channel}{event.reason ? <><br />이유 {event.reason}</> : null}</li>)}
              </ol>
            ) : (
              <p className="mt-3 text-sm text-[var(--color-revisit-text-helper)]">아직 남은 전달 기록이 없습니다. 이력은 지워지지 않고 쌓입니다.</p>
            )}

            {/* 정정·철회는 서버가 허용할 때만 보인다 — 유효한 전달 기록이 있고, 관리자 계정일 때. */}
            {delivered && (
              <div className="mt-4 border-t border-[var(--color-revisit-coolgrey-20)] pt-4">
                <h5 className="font-bold">전달 정보가 잘못됐다면</h5>
                {isOwner ? (
                  <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                    <button type="button" onClick={() => setMode(mode === 'correct' ? 'none' : 'correct')} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">전달 정보 수정 기록 추가</button>
                    <button type="button" onClick={() => setMode(mode === 'rescind' ? 'none' : 'rescind')} className="min-h-11 rounded-lg border border-[var(--color-revisit-red-50)] px-4 text-sm font-bold text-[var(--color-revisit-red-50)]">전달 기록 무효 처리</button>
                  </div>
                ) : (
                  <p className="mt-2 rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-sm leading-6">수정과 무효 처리는 관리자만 할 수 있습니다. 잘못된 기록이 있으면 관리자에게 이 보고서 기간과 받은 분을 알려 주세요.</p>
                )}
              </div>
            )}
            {delivered && mode === 'correct' && (
              <div className="mt-3 rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3">
                <p className="text-sm leading-6">기존 기록은 지우지 않고 수정 기록을 덧붙입니다.</p>
                <DeliveryFields recipient={recipient} channel={channel} note={note} onRecipient={setRecipient} onChannel={setChannel} onNote={setNote} />
                <Reason value={reason} onChange={setReason} label="수정 이유" />
                {!downloadedSha256 && <p className="mt-2 text-sm text-[var(--color-revisit-text-helper)]">{DOWNLOAD_REQUIRED}</p>}
                <button type="button" disabled={busy || !deliveryValid || !reasonValid || !downloadedSha256} onClick={() => downloadedSha256 && onAction({ kind: 'correct', artifactSha256: downloadedSha256, recipient: recipient.trim(), channel: channel.trim(), note: note.trim() || undefined, reason: reason.trim() })} className="mt-3 min-h-11 w-full rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white disabled:opacity-40">수정 기록 추가</button>
              </div>
            )}
            {delivered && mode === 'rescind' && (
              <div className="mt-3 rounded-lg border border-[var(--color-revisit-red-50)] p-3">
                <p className="text-sm leading-6"><strong>중요:</strong> 무효 처리는 이미 보낸 파일을 회수하지 않습니다. 원장님께 잘못 전달했다면 별도로 연락해 사용 중지를 안내하세요.</p>
                <Reason value={reason} onChange={setReason} label="무효 처리 이유" />
                <button type="button" disabled={busy || !reasonValid} onClick={() => onAction({ kind: 'rescind', reason: reason.trim() })} className="mt-3 min-h-11 w-full rounded-lg bg-[var(--color-revisit-red-50)] px-4 text-sm font-bold text-white disabled:opacity-40">이유를 남기고 기록 무효 처리</button>
              </div>
            )}
          </section>

          {/* 내부 검수 근거는 원장 대면 절차가 아니다. 필요한 사람만 펼쳐 본다. */}
          <details data-report-section="internal" className="rounded-xl border border-[var(--color-revisit-coolgrey-20)] p-4">
            <summary className="flex min-h-11 cursor-pointer items-center font-bold text-[var(--color-revisit-text-title)]">내부 상태 · 원장 전달 금지</summary>
            <div className="mt-4 space-y-5">
              {report.internalDownloadUrl && <a href={report.internalDownloadUrl} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center justify-center rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold text-[var(--color-revisit-text-helper)]">{getInternalReportLabel(true, report.hasPdf)}</a>}
              <ReportEvidence report={report} onCopyNotification={onCopyDeveloperInfo} />
            </div>
          </details>
        </div>
      </div>
    </div>,
    document.body,
  )
}

function DeliveryFields({ recipient, channel, note, onRecipient, onChannel, onNote }: { recipient: string; channel: string; note: string; onRecipient: (value: string) => void; onChannel: (value: string) => void; onNote: (value: string) => void }) {
  return <div className="mt-4 grid gap-3 sm:grid-cols-2"><Field label="받은 분" value={recipient} onChange={onRecipient} placeholder="예: 김 원장" /><Field label="전달 방법" value={channel} onChange={onChannel} placeholder="예: 대면, 이메일" /><div className="sm:col-span-2"><Field label="메모 (선택)" value={note} onChange={onNote} placeholder="예: 8월 보고" /></div></div>
}
function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder: string }) { return <label className="text-sm font-semibold">{label}<input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="mt-1 min-h-11 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-3 font-normal" /></label> }
function Reason({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) { return <label className="mt-3 block text-sm font-semibold">{label}<textarea value={value} onChange={(event) => onChange(event.target.value)} maxLength={1000} rows={2} className="mt-1 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] p-3 font-normal" /></label> }
function Copy({ label, value }: { label: string; value: string }) {
  return <div><dt className="font-bold text-[var(--color-revisit-text-title)]">{label}</dt><dd className="mt-1 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{value}</dd></div>
}
