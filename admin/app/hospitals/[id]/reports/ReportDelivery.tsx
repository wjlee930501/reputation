'use client'

import { useState } from 'react'
import {
  deliveryEventLabel,
  getDoctorDownload,
  getInternalReportLabel,
  isEffectivelyDelivered,
  latestDeliveryEvent,
} from '@/lib/report-delivery'
import type { ReportView } from '@/lib/report-review'

export type DeliveryAction =
  | { kind: 'deliver'; recipient: string; channel: string; note?: string }
  | { kind: 'correct'; recipient: string; channel: string; note?: string; reason: string }
  | { kind: 'rescind'; reason: string }

function format(value: string | null): string {
  return value ? new Date(value).toLocaleString('ko-KR') : '-'
}

export function ReportDelivery({
  report,
  isOwner,
  busy,
  onAction,
}: {
  report: ReportView
  isOwner: boolean
  busy: boolean
  onAction: (action: DeliveryAction) => void
}) {
  const delivered = isEffectivelyDelivered(report)
  const doctorUrl = getDoctorDownload(report.hospitalId, report.id, report.doctorArtifact.state, report)
  const current = latestDeliveryEvent(report.deliveryHistory)
  const [recipient, setRecipient] = useState(current?.recipient === '-' ? '' : current?.recipient ?? '')
  const [channel, setChannel] = useState(current?.channel === '-' ? '대면' : current?.channel ?? '대면')
  const [note, setNote] = useState('')
  const [reason, setReason] = useState('')
  const [mode, setMode] = useState<'none' | 'correct' | 'rescind'>('none')
  const deliveryValid = recipient.trim().length > 0 && channel.trim().length > 0
  const reasonValid = reason.trim().length >= 2

  return (
    <section className="rounded-xl border-2 border-[var(--color-revisit-primary-40)] p-4" aria-labelledby="delivery-heading" data-review-section="delivery">
      <p className="text-xs font-bold text-[var(--color-revisit-primary-40)]">근거 확인 후 실행</p>
      <h4 id="delivery-heading" className="mt-1 text-lg font-bold text-[var(--color-revisit-text-title)]">원장 전달과 이력 관리</h4>
      <p className="mt-2 text-sm leading-6 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">
        원장 전달용 파일이 첫 번째 자료입니다. 내부 검수용 리포트는 원장님께 보내지 마세요.
      </p>
      <div className="mt-4 grid gap-2">
        {doctorUrl ? (
          <a href={doctorUrl} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center justify-center rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white">원장 전달용 보고서 열기</a>
        ) : (
          <div className="rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-sm leading-6">
            <strong>문제:</strong> 검증된 원장 전달용 보고서를 열 수 없습니다.<br />
            <strong>고객 영향:</strong> 내부 검수용 파일로 대신 전달할 수 없습니다.<br />
            <strong>지금 할 일:</strong> 위의 파일 검증과 전달 차단 항목을 해결해 주세요.
          </div>
        )}
        {report.internalDownloadUrl && <a href={report.internalDownloadUrl} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center justify-center rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold text-[var(--color-revisit-text-helper)]">{getInternalReportLabel(true, report.hasPdf)}</a>}
      </div>

      {delivered ? (
        <div className="mt-4 rounded-lg border border-[var(--color-revisit-green-50)] p-3 text-sm leading-6">
          <strong>현재 유효한 전달 기록</strong>
          <p className="mt-1">{current ? deliveryEventLabel(current.type) : '전달 기록'} · {format(current?.createdAt ?? report.sentAt)}</p>
          <p>수신자 {current?.recipient ?? '-'} · 전달 방법 {current?.channel ?? '-'}</p>
          <p className="mt-1 text-xs text-[var(--color-revisit-text-helper)]">전달 당시 검증본 확인 번호 · 앞 12자리 {report.doctorArtifact.sha256?.slice(0, 12) ?? '확인 불가'} · {report.review?.versionLabel ?? '버전 확인 불가'}</p>
        </div>
      ) : (
        <DeliveryFields recipient={recipient} channel={channel} note={note} onRecipient={setRecipient} onChannel={setChannel} onNote={setNote} />
      )}

      {!delivered && (
        <button type="button" disabled={busy || !doctorUrl || !deliveryValid} onClick={() => onAction({ kind: 'deliver', recipient: recipient.trim(), channel: channel.trim(), note: note.trim() || undefined })} className="mt-3 min-h-11 w-full rounded-lg bg-[var(--color-revisit-green-50)] px-4 text-sm font-bold text-white disabled:opacity-40">
          {busy ? '최신 상태 확인 중' : report.deliveryReady ? '이 파일의 원장 전달 기록 남기기' : '차단 항목을 해결한 뒤 기록할 수 있습니다'}
        </button>
      )}

      {delivered && (
        <div className="mt-4 border-t border-[var(--color-revisit-coolgrey-20)] pt-4">
          <h5 className="font-bold">전달 정보가 잘못됐다면</h5>
          {isOwner ? (
            <div className="mt-2 flex flex-col gap-2 sm:flex-row">
              <button type="button" onClick={() => setMode(mode === 'correct' ? 'none' : 'correct')} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 text-sm font-bold">전달 정보 수정 기록 추가</button>
              <button type="button" onClick={() => setMode(mode === 'rescind' ? 'none' : 'rescind')} className="min-h-11 rounded-lg border border-[var(--color-revisit-red-50)] px-4 text-sm font-bold text-[var(--color-revisit-red-50)]">전달 기록 무효 처리</button>
            </div>
          ) : (
            <p className="mt-2 rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-sm leading-6">수정과 무효 처리는 관리자만 할 수 있습니다. 잘못된 기록이 있으면 관리자에게 이 리포트 기간과 수신자를 알려 주세요.</p>
          )}
        </div>
      )}

      {mode === 'correct' && (
        <div className="mt-3 rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3">
          <p className="text-sm leading-6">기존 기록은 지우지 않고 수정 기록을 덧붙입니다.</p>
          <DeliveryFields recipient={recipient} channel={channel} note={note} onRecipient={setRecipient} onChannel={setChannel} onNote={setNote} />
          <Reason value={reason} onChange={setReason} label="수정 이유" />
          <button type="button" disabled={busy || !deliveryValid || !reasonValid} onClick={() => onAction({ kind: 'correct', recipient: recipient.trim(), channel: channel.trim(), note: note.trim() || undefined, reason: reason.trim() })} className="mt-3 min-h-11 w-full rounded-lg bg-[var(--color-revisit-primary-40)] px-4 text-sm font-bold text-white disabled:opacity-40">수정 기록 추가</button>
        </div>
      )}
      {mode === 'rescind' && (
        <div className="mt-3 rounded-lg border border-[var(--color-revisit-red-50)] p-3">
          <p className="text-sm leading-6"><strong>중요:</strong> 무효 처리는 이미 보낸 파일을 회수하지 않습니다. 원장님께 잘못 전달했다면 별도로 연락해 사용 중지를 안내하세요.</p>
          <Reason value={reason} onChange={setReason} label="무효 처리 이유" />
          <button type="button" disabled={busy || !reasonValid} onClick={() => onAction({ kind: 'rescind', reason: reason.trim() })} className="mt-3 min-h-11 w-full rounded-lg bg-[var(--color-revisit-red-50)] px-4 text-sm font-bold text-white disabled:opacity-40">이유를 남기고 기록 무효 처리</button>
        </div>
      )}

      {report.deliveryHistory.length > 0 && (
        <div className="mt-5 border-t border-[var(--color-revisit-coolgrey-20)] pt-4">
          <h5 className="font-bold">삭제되지 않는 전달 이력</h5>
          <ol className="mt-2 grid gap-2">
            {report.deliveryHistory.map((event) => <li key={event.id} className="rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-sm leading-5"><strong>{deliveryEventLabel(event.type)}</strong> · {format(event.createdAt)}<br />수신자 {event.recipient} · 전달 방법 {event.channel}{event.reason ? <><br />이유 {event.reason}</> : null}</li>)}
          </ol>
        </div>
      )}
    </section>
  )
}

function DeliveryFields({ recipient, channel, note, onRecipient, onChannel, onNote }: { recipient: string; channel: string; note: string; onRecipient: (value: string) => void; onChannel: (value: string) => void; onNote: (value: string) => void }) {
  return <div className="mt-4 grid gap-3 sm:grid-cols-2"><Field label="받은 분" value={recipient} onChange={onRecipient} placeholder="예: 김 원장" /><Field label="전달 방법" value={channel} onChange={onChannel} placeholder="예: 대면, 이메일" /><div className="sm:col-span-2"><Field label="메모 (선택)" value={note} onChange={onNote} placeholder="예: 8월 월간 보고" /></div></div>
}
function Field({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder: string }) { return <label className="text-sm font-semibold">{label}<input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} className="mt-1 min-h-11 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-3 font-normal" /></label> }
function Reason({ value, onChange, label }: { value: string; onChange: (value: string) => void; label: string }) { return <label className="mt-3 block text-sm font-semibold">{label}<textarea value={value} onChange={(event) => onChange(event.target.value)} maxLength={1000} rows={2} className="mt-1 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] p-3 font-normal" /></label> }
