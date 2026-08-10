'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

import {
  buildDevelopmentSupportSummary,
  enabledPostAction,
  historyEventLabel,
  operationStatusLabel,
  runStateLabel,
  shouldPollRun,
  slackStateLabel,
} from '@/lib/operations-center'
import type { OperationsIncidentDetail, OperationsQueueRow } from '@/types'

export type OperationMutation = {
  readonly kind: 'RETRY_RUN' | 'RECOVER_INCIDENT' | 'ACK_INCIDENT' | 'RETRY_SLACK'
  readonly path: string
  readonly targetId: string
  readonly version: number | null
  readonly reason: string
}

type Props = {
  readonly detail: OperationsIncidentDetail | null
  readonly fallback: OperationsQueueRow | null
  readonly busy: boolean
  readonly error: string
  readonly permissionDenied: boolean
  readonly onClose: () => void
  readonly onMutate: (mutation: OperationMutation) => void
}

function formatDate(value: string | null): string {
  if (!value) return '기록 없음'
  return new Intl.DateTimeFormat('ko-KR', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date(value))
}

function incidentBase(row: OperationsQueueRow): string {
  const hospital = row.customer.hospital_id
  return hospital
    ? `/admin/operations/hospitals/${hospital}/incidents/${row.incident_id}`
    : `/admin/operations/incidents/${row.incident_id}`
}

function isOperatorNavigation(row: OperationsQueueRow): boolean {
  return row.action.enabled
    && row.action.method === 'GET'
    && (row.action.path.startsWith('/hospitals/') || row.action.path.startsWith('/leads/'))
}

function primaryMutation(detail: OperationsIncidentDetail, reason: string): OperationMutation | null {
  const row = detail.incident
  const run = detail.run
  const retry = enabledPostAction(run?.retry ?? null) ?? enabledPostAction(row.retry)
  if (retry?.kind === 'RETRY_RUN') {
    return { kind: 'RETRY_RUN', path: retry.path, targetId: run?.run_id ?? row.operation_run_id ?? row.id, version: null, reason }
  }
  const action = enabledPostAction(row.action)
  if (action?.kind === 'RECOVER_INCIDENT' || action?.kind === 'ACK_INCIDENT') {
    return { kind: action.kind, path: action.path, targetId: row.incident_id ?? row.id, version: row.version, reason }
  }
  if (run?.state === 'SUCCEEDED' && row.status === 'RETRYING') {
    return { kind: 'RECOVER_INCIDENT', path: `${incidentBase(row)}/recover`, targetId: row.incident_id ?? row.id, version: row.version, reason }
  }
  if (row.status === 'RECOVERED') {
    return { kind: 'ACK_INCIDENT', path: `${incidentBase(row)}/ack`, targetId: row.incident_id ?? row.id, version: row.version, reason }
  }
  return null
}

function mutationLabel(mutation: OperationMutation): string {
  switch (mutation.kind) {
    case 'RETRY_RUN': return '작업 다시 시도'
    case 'RECOVER_INCIDENT': return '복구 확인 완료'
    case 'ACK_INCIDENT': return '문제 확인 완료'
    case 'RETRY_SLACK': return 'Slack 다시 보내기'
  }
}

export function OperationDetail(props: Props) {
  const { detail, fallback, busy, error, permissionDenied, onClose, onMutate } = props
  const [reason, setReason] = useState('')
  const [copyStatus, setCopyStatus] = useState('')
  const copyButton = useRef<HTMLButtonElement>(null)
  const row = detail?.incident ?? fallback
  useEffect(() => {
    setReason('')
    setCopyStatus('')
  }, [row?.id])
  useEffect(() => {
    if (permissionDenied) window.requestAnimationFrame(() => copyButton.current?.focus())
  }, [permissionDenied])
  if (!row) {
    return <aside className="ops-detail ops-detail--empty" aria-label="작업 상세"><p className="font-semibold text-slate-700">목록에서 작업을 선택하면 처리 순서와 이력이 표시됩니다.</p></aside>
  }

  const effectiveDetail: OperationsIncidentDetail = detail ?? { incident: row, run: null }
  const run = effectiveDetail.run
  const slack = row.slack
  const hospitalId = row.customer.hospital_id
  const runAutomatic = Boolean(run && shouldPollRun(run.state))
  const automatic = runAutomatic || slack?.state === 'RETRYING'
  const slackRetry: OperationMutation | null = slack?.state === 'FAILED' && !runAutomatic ? {
    kind: 'RETRY_SLACK',
    path: hospitalId
      ? `/admin/operations/hospitals/${hospitalId}/notifications/${slack.notification_id}/retry`
      : `/admin/operations/notifications/${slack.notification_id}/retry`,
    targetId: slack.notification_id, version: slack.version, reason,
  } : null
  const mutation = primaryMutation(effectiveDetail, reason) ?? slackRetry
  const directLink = !mutation && isOperatorNavigation(row)
  const waitUntil = slack?.state === 'RETRYING' && slack.next_attempt_at
    ? `${formatDate(slack.next_attempt_at)}까지`
    : '화면이 최대 3초 안에 한 번 더 갱신될 때까지'

  const copyForDevelopment = async () => {
    try {
      await navigator.clipboard.writeText(buildDevelopmentSupportSummary(effectiveDetail, window.location.origin))
      setCopyStatus('개발팀 문의용 정보가 복사되었습니다.')
    } catch {
      setCopyStatus('복사하지 못했습니다. 브라우저의 클립보드 권한을 확인해 주세요.')
    }
    window.requestAnimationFrame(() => copyButton.current?.focus())
  }

  return (
    <aside className="ops-detail" aria-labelledby="ops-detail-title">
      <div className="flex items-start justify-between gap-3 border-b border-slate-200 pb-3">
        <div className="min-w-0"><p className="text-xs font-bold text-blue-700">선택한 작업</p><h2 id="ops-detail-title" className="ops-readable mt-1 text-lg font-bold text-slate-950">{row.customer.name}</h2></div>
        <button type="button" onClick={onClose} className="ops-control shrink-0 rounded-lg border border-slate-300 px-3 text-sm font-semibold text-slate-600">닫기</button>
      </div>

      <section className="ops-detail-section">
        <h3>무슨 문제인지</h3>
        <p className="text-sm font-semibold text-slate-800">{operationStatusLabel(row.status)}</p>
        <p className="ops-readable mt-2 rounded-lg bg-slate-50 px-3 py-2 text-sm leading-6 text-slate-600">{row.safe_cause ?? '상세 원인 기록을 확인하는 단계입니다.'}</p>
      </section>

      <section className="ops-detail-section">
        <h3>고객 영향</h3>
        <p className="ops-readable text-sm leading-6 text-slate-700">{row.impact}</p>
      </section>

      <section className="ops-detail-section">
        <h3>지금 할 일</h3>
        <p className="ops-readable text-sm leading-6 text-slate-700">{row.next_action}</p>
        <p className="ops-readable mt-1 text-xs leading-5 text-slate-500">현재 상태를 확인하고 다음 운영 단계의 근거를 남기기 위해 필요한 작업입니다.</p>
        {automatic ? <p className="ops-readable mt-2 rounded-lg bg-blue-50 px-3 py-2 text-sm leading-6 text-blue-800">자동 재시도 중입니다. {waitUntil} 기다려 주세요. 그 뒤에도 상태가 같으면 아래 개발팀 문의용 정보를 복사해 전달해 주세요.</p> : null}
        {mutation ? (
          <div className="mt-3">
            <label className="block text-xs font-semibold text-slate-600" htmlFor="ops-action-reason">처리 사유</label>
            <textarea id="ops-action-reason" value={reason} onChange={(event) => setReason(event.target.value)} rows={2} maxLength={200} placeholder="3자 이상 기록해 주세요" className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm" />
            <button type="button" disabled={busy || reason.trim().length < 3} onClick={() => onMutate(mutation)} className="ops-control ops-primary-action mt-2 w-full rounded-lg px-4 text-sm font-bold text-white disabled:cursor-not-allowed disabled:opacity-45">{busy ? '서버 확인 중…' : mutationLabel(mutation)}</button>
          </div>
        ) : directLink ? (
          <Link href={row.action.path} className="ops-control mt-3 inline-flex w-full items-center justify-center rounded-lg border border-blue-300 bg-blue-50 px-4 text-sm font-bold text-blue-800">{row.action.label}</Link>
        ) : (
          <button ref={copyButton} type="button" onClick={copyForDevelopment} className="ops-control mt-3 w-full rounded-lg border border-slate-300 bg-white px-4 text-sm font-bold text-slate-700">개발팀 문의용 정보 복사</button>
        )}
        {copyStatus ? <p role="status" aria-live="polite" className="mt-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{copyStatus}</p> : null}
        {error ? <p role="alert" className="ops-readable mt-2 rounded-lg bg-red-50 px-3 py-2 text-sm leading-5 text-red-700">{error}</p> : null}
        {mutation && permissionDenied ? <button ref={copyButton} type="button" onClick={copyForDevelopment} className="ops-control mt-2 w-full rounded-lg border border-slate-300 bg-white px-4 text-sm font-bold text-slate-700">개발팀 문의용 정보 복사</button> : null}
      </section>

      <section className="ops-detail-section">
        <h3>작업 진행</h3>
        {run ? <ol className="ops-timeline"><li><b>요청</b><span>{formatDate(run.requested_at)}</span></li><li><b>대기</b><span>{formatDate(run.queued_at)}</span></li><li><b>실행</b><span>{formatDate(run.started_at)}</span></li><li><b>{runStateLabel(run.state)}</b><span>{formatDate(run.completed_at)}</span></li></ol> : <p className="text-sm text-slate-500">연결된 자동 작업 기록이 없습니다.</p>}
      </section>

      <section className="ops-detail-section">
        <h3>Slack 상태</h3>
        <p className="text-sm font-semibold text-slate-800">{slack ? slackStateLabel(slack.state) : '연결된 알림 없음'}</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">Slack 발송 상태는 고객 작업의 성공 여부와 별개입니다.</p>
        {slack?.state === 'HOLD' ? <p className="mt-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">발송 결과가 불확실해 자동 재시도하지 않습니다. Slack에서 중복 여부를 먼저 확인해 주세요.</p> : null}
      </section>

      <section className="ops-detail-section"><h3>문제 이력</h3>{row.history.length ? <ol className="space-y-2">{row.history.map((entry) => <li key={`${entry.event}-${entry.at}`} className="flex justify-between gap-3 text-sm"><span className="font-semibold text-slate-700">{historyEventLabel(entry.event)}</span><time className="shrink-0 text-slate-500">{formatDate(entry.at)}</time></li>)}</ol> : <p className="text-sm text-slate-500">기록된 문제 이력이 없습니다.</p>}</section>
    </aside>
  )
}
