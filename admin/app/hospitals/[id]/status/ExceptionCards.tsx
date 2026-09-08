'use client'

import Link from 'next/link'
import { useRef, useState } from 'react'
import { ApiError, fetchAPI } from '@/lib/api'
import { createUserActionKey, interpretOperationsConflict } from '@/lib/operations-center'
import { getOrCreatePendingActionKey } from '@/lib/pending-action-key'
import { cardButtons, mutationBodyFor } from '@/lib/status-screen'
import type {
  HospitalOverviewException,
  OperationsAction,
  OperationsIncidentDetail,
} from '@/types'
import { EscalatedDraftCard } from './EscalatedDraftCard'

const PERMISSION_DENIED =
  '이 작업은 권한 있는 담당자만 처리할 수 있습니다. 담당자에게 요청하거나 개발팀 문의 정보를 복사하세요.'
const MIN_REASON_LENGTH = 3

export function ExceptionCards({
  exceptions,
  onDone,
}: {
  exceptions: HospitalOverviewException[]
  onDone: () => Promise<void>
}) {
  return (
    <section aria-label="사람이 결정할 항목" className="space-y-3">
      <h2 className="text-sm font-semibold text-slate-900">사람이 결정할 항목</h2>
      {exceptions.length === 0 ? (
        <p className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-500">
          지금 사람이 결정할 항목이 없습니다.
        </p>
      ) : (
        exceptions.map((exception) =>
          exception.kind === 'escalated_draft' ? (
            <EscalatedDraftCard key={exception.id} exception={exception} onDone={onDone} />
          ) : (
            <IncidentCard key={exception.id} exception={exception} onDone={onDone} />
          ),
        )
      )}
    </section>
  )
}

function IncidentCard({
  exception,
  onDone,
}: {
  exception: HospitalOverviewException
  onDone: () => Promise<void>
}) {
  const buttons = cardButtons(exception)
  const [openAction, setOpenAction] = useState<OperationsAction | null>(null)
  const [reason, setReason] = useState('')
  const [ownerId, setOwnerId] = useState('')
  const [detail, setDetail] = useState<OperationsIncidentDetail | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // 결과를 모른 채 같은 변경을 두 번 사지 않도록, 확정 응답을 받을 때까지 요청 키를 붙든다.
  const mutationKeys = useRef(new Map<string, string>())

  async function openForm(action: OperationsAction) {
    setOpenAction(action)
    setReason('')
    setError(null)
    if (action.kind !== 'ASSIGN_INCIDENT' || detail || !exception.incident_id) return
    try {
      const loaded = await fetchAPI<OperationsIncidentDetail>(
        `/admin/operations/hospitals/${exception.hospital_id}/incidents/${exception.incident_id}`,
      )
      setDetail(loaded)
      setOwnerId(loaded.incident.owner?.id ?? '')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '담당자 목록을 불러오지 못했습니다.')
    }
  }

  async function submit(action: OperationsAction) {
    const targetId = exception.operation_run_id ?? exception.incident_id ?? exception.id
    // 같은 대상·같은 사유의 재시도는 같은 요청으로 본다(운영 센터의 지문과 같은 값).
    const fingerprint = JSON.stringify([
      action.kind,
      action.path,
      targetId,
      exception.version,
      reason.trim(),
    ])
    const key = action.requires_idempotency_key
      ? getOrCreatePendingActionKey(mutationKeys.current, fingerprint, () =>
          createUserActionKey(action.kind, targetId, crypto.randomUUID()),
        )
      : null
    setBusy(true)
    setError(null)
    try {
      await fetchAPI(action.path, {
        method: action.method,
        headers: key ? { 'Idempotency-Key': key } : undefined,
        body: JSON.stringify(
          mutationBodyFor(action, {
            reason,
            version: exception.version,
            ownerId: ownerId || null,
            slaDueAt: detail?.incident.sla_due_at ?? null,
          }),
        ),
      })
      // 응답을 받았으면 이 요청의 결과는 확정됐다 — 요청 키의 수명을 여기서 끝낸다.
      mutationKeys.current.delete(fingerprint)
      setOpenAction(null)
      setReason('')
      // 상태가 바뀌었으니 들고 있던 상세(담당 후보·처리 기한)는 버린다.
      setDetail(null)
      await onDone()
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        mutationKeys.current.delete(fingerprint)
        setError(interpretOperationsConflict(e.detail).message)
        await onDone()
      } else if (e instanceof ApiError && e.status === 403) {
        mutationKeys.current.delete(fingerprint)
        setError(PERMISSION_DENIED)
      } else {
        setError(e instanceof Error ? e.message : '요청을 처리하지 못했습니다.')
      }
    } finally {
      setBusy(false)
    }
  }

  const assignable = detail?.assignable_accounts ?? []

  return (
    <article className="rounded-xl border border-amber-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">{exception.title}</h3>
      {exception.evidence && (
        <p className="mt-1 whitespace-pre-line text-xs text-slate-600">{exception.evidence}</p>
      )}
      <p className="mt-2 text-sm text-slate-800">{exception.next_action}</p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Link
          href={exception.href}
          className="inline-flex min-h-11 items-center rounded-lg border border-slate-300 px-3 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          운영 센터에서 보기
        </Link>
        {buttons.enabled.map((action) => (
          <button
            key={action.kind}
            type="button"
            onClick={() => void openForm(action)}
            className="inline-flex min-h-11 items-center rounded-lg border border-blue-300 bg-blue-50 px-3 text-xs font-semibold text-blue-800 hover:bg-blue-100"
          >
            {action.label}
          </button>
        ))}
      </div>

      {buttons.disabled.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {buttons.disabled.map(({ action, reason: why }) => (
            <li key={action.kind} className="text-[11px] text-slate-500">
              {action.label} — {why}
            </li>
          ))}
        </ul>
      )}

      {openAction && (
        <div className="mt-3 space-y-2 rounded-lg border border-slate-200 bg-slate-50 p-3">
          <p className="text-xs font-semibold text-slate-700">{openAction.label}</p>
          {openAction.kind === 'ASSIGN_INCIDENT' && (
            <label className="block text-xs text-slate-600">
              담당자
              <select
                value={ownerId}
                onChange={(event) => setOwnerId(event.target.value)}
                className="mt-1 block min-h-11 w-full rounded-lg border border-slate-300 bg-white px-2 text-sm"
              >
                <option value="">담당 없음</option>
                {assignable.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name} ({account.email})
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="block text-xs text-slate-600">
            사유 ({MIN_REASON_LENGTH}자 이상)
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              rows={2}
              className="mt-1 block w-full resize-none rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
            />
          </label>
          {error && <p className="whitespace-pre-line text-xs text-red-600">{error}</p>}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void submit(openAction)}
              disabled={busy || reason.trim().length < MIN_REASON_LENGTH}
              className="inline-flex min-h-11 items-center rounded-lg bg-blue-600 px-3 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {busy ? '처리 중...' : '확인'}
            </button>
            <button
              type="button"
              onClick={() => {
                setOpenAction(null)
                setError(null)
              }}
              className="inline-flex min-h-11 items-center rounded-lg border border-slate-300 px-3 text-xs font-medium text-slate-700"
            >
              닫기
            </button>
          </div>
        </div>
      )}
      {!openAction && error && (
        <p className="mt-2 whitespace-pre-line text-xs text-red-600">{error}</p>
      )}
    </article>
  )
}
