'use client'

import Link from 'next/link'
import { useEffect, useState } from 'react'
import { fetchAPI } from '@/lib/api'
import { fetchCurrentAccount } from '@/lib/current-account'
import {
  RE_REVIEW_CONFIRM,
  approveErrorMessage,
  philosophyApproveBody,
  philosophyDraftText,
  philosophyPatchBody,
  reReviewErrorMessage,
  reReviewNotice,
  type PhilosophyDraftText,
} from '@/lib/essence-actions'
import { persistThenApprove } from '@/lib/operator-safety'
import type { ContentPhilosophy, HospitalOverviewException } from '@/types'

const FIELDS: Array<{ key: keyof PhilosophyDraftText; label: string; rows: number; list: boolean }> = [
  { key: 'positioning', label: '병원이 알려져야 할 기준', rows: 3, list: false },
  { key: 'voice', label: '원장님 말투와 설명 방식', rows: 3, list: false },
  { key: 'promise', label: '환자에게 일관되게 전달할 약속', rows: 3, list: false },
  { key: 'principles', label: '콘텐츠 작성 원칙', rows: 4, list: true },
  { key: 'tone', label: '말투 기준', rows: 3, list: true },
  { key: 'mustUse', label: '반드시 담을 메시지', rows: 3, list: true },
  { key: 'avoid', label: '피해야 할 표현', rows: 3, list: true },
  { key: 'riskRules', label: '의료광고 리스크 규칙', rows: 3, list: true },
]

const EMPTY_DRAFT: PhilosophyDraftText = {
  positioning: '',
  voice: '',
  promise: '',
  principles: '',
  tone: '',
  mustUse: '',
  avoid: '',
  riskRules: '',
}

const MIN_OVERRIDE_LENGTH = 20

export function EscalatedDraftCard({
  exception,
  onDone,
}: {
  exception: HospitalOverviewException
  onDone: () => Promise<void>
}) {
  const [draft, setDraft] = useState<ContentPhilosophy | null>(null)
  const [fields, setFields] = useState<PhilosophyDraftText>(EMPTY_DRAFT)
  const [reviewedBy, setReviewedBy] = useState('')
  const [approvalNote, setApprovalNote] = useState('')
  const [overrideReason, setOverrideReason] = useState('')
  const [confirmEvidence, setConfirmEvidence] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const hospitalId = exception.hospital_id
  const draftId = exception.id
  const draftPath = `/admin/hospitals/${hospitalId}/essence/philosophy/${draftId}`
  const findings = (exception.evidence ?? '').split('\n').filter(Boolean)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const philosophies = await fetchAPI<ContentPhilosophy[]>(
          `/admin/hospitals/${hospitalId}/essence/philosophies`,
        )
        if (!alive) return
        const found = philosophies.find((item) => item.id === draftId) ?? null
        setDraft(found)
        setFields(found ? philosophyDraftText(found) : EMPTY_DRAFT)
      } catch (e: unknown) {
        if (alive) setError(e instanceof Error ? e.message : '초안을 불러오지 못했습니다.')
      }
    }
    void load()
    void fetchCurrentAccount().then((account) => {
      if (alive) setReviewedBy(account?.name ?? '')
    })
    return () => {
      alive = false
    }
  }, [hospitalId, draftId])

  async function persistDraft(): Promise<void> {
    await fetchAPI<ContentPhilosophy>(draftPath, {
      method: 'PATCH',
      body: JSON.stringify(philosophyPatchBody(fields)),
    })
  }

  async function saveDraft() {
    setBusy('save')
    setError(null)
    setNotice(null)
    try {
      await persistDraft()
      setNotice('초안을 저장했습니다.')
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '초안 저장에 실패했습니다.')
    } finally {
      setBusy(null)
    }
  }

  async function reReview() {
    if (!confirm(RE_REVIEW_CONFIRM)) return
    setBusy('re-review')
    setError(null)
    setNotice(null)
    try {
      const result = await fetchAPI<ContentPhilosophy>(`${draftPath}/re-review`, {
        method: 'POST',
      })
      setNotice(reReviewNotice(result))
      await onDone()
    } catch (e: unknown) {
      setError(reReviewErrorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  async function approve() {
    setBusy('approve')
    setError(null)
    setNotice(null)
    try {
      // 화면에 보이는 문장이 승인된 문장이어야 한다 — 저장 뒤에 승인한다.
      await persistThenApprove(persistDraft, () =>
        fetchAPI(`${draftPath}/approve`, {
          method: 'POST',
          body: JSON.stringify(
            philosophyApproveBody({ reviewedBy, approvalNote, confirmEvidence, overrideReason }),
          ),
        }),
      )
      setNotice('콘텐츠 운영 기준이 승인되었습니다. 자동 콘텐츠 생성에 적용됩니다.')
      await onDone()
    } catch (e: unknown) {
      setError(approveErrorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  const approveBlocked =
    !draft ||
    draft.status !== 'DRAFT' ||
    !confirmEvidence ||
    !reviewedBy.trim() ||
    (findings.length > 0 && overrideReason.trim().length < MIN_OVERRIDE_LENGTH)

  return (
    <article className="rounded-xl border border-amber-200 bg-white p-4">
      <h3 className="text-sm font-semibold text-slate-900">{exception.title}</h3>
      {findings.length > 0 && (
        <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-amber-800">
          {findings.map((finding) => (
            <li key={finding}>{finding}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-sm text-slate-800">{exception.next_action}</p>
      <Link
        href={exception.href}
        className="mt-2 inline-flex min-h-11 items-center text-xs text-blue-700 underline underline-offset-2"
      >
        {'콘텐츠 운영 기준 화면에서 자료까지 보기'}
      </Link>

      {draft === null ? (
        <p className="mt-3 text-xs text-slate-500">초안을 불러오는 중입니다.</p>
      ) : (
        <>
          <details className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
            <summary className="cursor-pointer text-xs font-semibold text-slate-700">초안 편집</summary>
            <p className="mt-1 text-[11px] text-slate-500">
              한 줄에 하나씩 적은 항목은 목록으로 저장됩니다.
            </p>
            <div className="mt-2 space-y-2">
              {FIELDS.map((field) => (
                <label key={field.key} className="block text-xs text-slate-600">
                  {field.label}
                  {field.list && <span className="text-slate-400"> (한 줄에 하나)</span>}
                  <textarea
                    value={fields[field.key]}
                    onChange={(event) =>
                      setFields((previous) => ({ ...previous, [field.key]: event.target.value }))
                    }
                    rows={field.rows}
                    disabled={draft.status !== 'DRAFT'}
                    className="mt-1 block w-full resize-none rounded-lg border border-slate-300 px-2 py-1.5 text-sm disabled:bg-slate-100"
                  />
                </label>
              ))}
            </div>
            <button
              type="button"
              onClick={() => void saveDraft()}
              disabled={busy !== null || draft.status !== 'DRAFT'}
              className="mt-2 inline-flex min-h-11 items-center rounded-lg border border-slate-300 bg-white px-3 text-xs font-semibold text-slate-700 disabled:opacity-50"
            >
              {busy === 'save' ? '저장 중...' : '초안 저장'}
            </button>
          </details>

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => void reReview()}
              disabled={busy !== null}
              className="inline-flex min-h-11 items-center rounded-lg border border-blue-300 bg-blue-50 px-3 text-xs font-semibold text-blue-800 disabled:opacity-50"
            >
              {busy === 're-review' ? '요청 중...' : '자료 기준 자동 재검수'}
            </button>
          </div>

          <div className="mt-3 space-y-2 rounded-lg border border-emerald-200 p-3">
            <p className="text-xs font-semibold text-slate-800">예외 승인</p>
            <p className="text-[11px] text-slate-500">
              검토자 {reviewedBy || '로그인 계정을 확인하는 중입니다.'} — 로그인한 계정으로 승인 기록이 남습니다.
            </p>
            <label className="block text-xs text-slate-600">
              승인 메모
              <textarea
                value={approvalNote}
                onChange={(event) => setApprovalNote(event.target.value)}
                rows={2}
                className="mt-1 block w-full resize-none rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
              />
            </label>
            {findings.length > 0 && (
              <label className="block text-xs text-slate-600">
                자동 검수 보류 사유별 확인 근거 ({MIN_OVERRIDE_LENGTH}자 이상, 필수)
                <textarea
                  value={overrideReason}
                  onChange={(event) => setOverrideReason(event.target.value)}
                  rows={3}
                  className="mt-1 block w-full resize-none rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
                />
              </label>
            )}
            <label className="flex items-start gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={confirmEvidence}
                onChange={(event) => setConfirmEvidence(event.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-slate-300"
              />
              근거 노트와 원문 발췌를 검토했습니다.
            </label>
            <button
              type="button"
              onClick={() => void approve()}
              disabled={busy !== null || approveBlocked}
              className="inline-flex min-h-11 w-full items-center justify-center rounded-lg bg-emerald-600 px-3 text-xs font-semibold text-white disabled:opacity-50"
            >
              {busy === 'approve' ? '예외 승인 중...' : '예외 승인'}
            </button>
          </div>
        </>
      )}

      {notice && <p className="mt-2 text-xs text-emerald-700">{notice}</p>}
      {error && <p className="mt-2 whitespace-pre-line text-xs text-red-600">{error}</p>}
    </article>
  )
}
