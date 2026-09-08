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
import { requiredSourceApprovalBlockers } from '@/lib/essence-evidence'
import { persistThenApprove } from '@/lib/operator-safety'
import type { ContentPhilosophy, HospitalOverviewException } from '@/types'

/** 근거 게이트가 읽는 자료의 최소 형태 — 운영 기준 화면과 같은 목록 응답이다. */
interface EvidenceSourceRow {
  status: string
  source_type: string
  raw_text?: string | null
  evidence_note_count: number
}

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
  onNotice,
}: {
  exception: HospitalOverviewException
  onDone: () => Promise<void>
  /**
   * 카드가 사라진 뒤에도 남아야 하는 안내. 재검수·승인은 예외 카드 자체를 없애므로,
   * 카드 안에만 적은 결과 문구는 화면이 갱신되는 순간 함께 사라진다.
   */
  onNotice?: (text: string) => void
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
  const [sources, setSources] = useState<EvidenceSourceRow[] | null>(null)
  const [sourcesLoading, setSourcesLoading] = useState(true)

  function publishNotice(text: string) {
    if (onNotice) onNotice(text)
    else setNotice(text)
  }

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
    // 예외 승인은 근거를 확인했다는 선언이다. 자료 상태를 읽지 못한 채로 열어 두면
    // 확인할 수 없는 것을 확인했다고 체크하는 승인이 된다(운영 기준 화면과 같은 게이트).
    async function loadSources() {
      try {
        const rows = await fetchAPI<EvidenceSourceRow[]>(
          `/admin/hospitals/${hospitalId}/essence/sources`,
        )
        if (alive) setSources(rows)
      } catch {
        if (alive) setSources(null)
      } finally {
        if (alive) setSourcesLoading(false)
      }
    }
    void load()
    void loadSources()
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
      publishNotice(reReviewNotice(result))
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
      publishNotice('콘텐츠 운영 기준이 승인되었습니다. 자동 콘텐츠 생성에 적용됩니다.')
      await onDone()
    } catch (e: unknown) {
      setError(approveErrorMessage(e))
    } finally {
      setBusy(null)
    }
  }

  // 운영 기준 화면(`essence/page.tsx`)의 승인 잠금과 같은 근거 게이트다.
  const evidenceBlockers = requiredSourceApprovalBlockers({
    sources,
    loading: sourcesLoading,
  })
  const approveBlocked =
    !draft ||
    draft.status !== 'DRAFT' ||
    evidenceBlockers.length > 0 ||
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
                disabled={evidenceBlockers.length > 0}
                className="mt-0.5 h-4 w-4 rounded border-slate-300 disabled:opacity-50"
              />
              근거 노트와 원문 발췌를 검토했습니다.
            </label>
            {evidenceBlockers.length > 0 && (
              <ul className="space-y-1 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-800">
                {evidenceBlockers.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
            <Link
              href={`/hospitals/${hospitalId}/info#info-sources`}
              className="inline-flex min-h-11 items-center text-xs text-blue-700 underline underline-offset-2"
            >
              근거 자료 보기
            </Link>
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
