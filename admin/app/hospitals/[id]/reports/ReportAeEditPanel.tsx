'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { ApiError, fetchAPI } from '@/lib/api'
import { getOrCreatePendingActionKey } from '@/lib/pending-action-key'
import {
  AE_CARD_NAMES_PATH,
  aeEditFingerprint,
  aeEditIdempotencyKey,
  buildAeEditFields,
  caseInsightPath,
  caseRecordPath,
  draftFromSummary,
  hasAeEditChanges,
  keyLinePath,
  parseAeEditPreview,
  planActionPath,
  problemsByPath,
  problemsFromRejection,
  treatmentLabelPath,
  type AeEditDraft,
  type AeEditPreview,
  type AeEditProblem,
} from '@/lib/report-ae-edit'
import { parseReportRuns } from '@/lib/report-run'

type Validation = { readonly fingerprint: string; readonly problems: readonly AeEditProblem[] }

const INPUT = 'mt-1 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white p-2 text-sm font-normal'
const BUTTON = 'min-h-11 rounded-lg px-4 text-sm font-bold disabled:opacity-40'

/**
 * 보고서 AE 수정 판 — 서술 문구·진료 항목 라벨·대표 사례 선택만 고쳐 새 버전을 만든다.
 *
 * 숫자는 입력란이 없다. 검증(금지 표현·내부 표식·새 숫자·숫자 보존)은 서버가 하고, 이 화면은
 * 저장 전에 같은 검증을 미리보기로 받아 칸 옆에 보여 준다. 새 버전은 다시 전달해야 반영된다.
 */
export function ReportAeEditPanel({
  hospitalId,
  reportId,
  onRequested,
}: {
  hospitalId: string
  reportId: string
  /** 새 버전 생성을 요청한 뒤 — 목록·작업 기록을 다시 읽게 한다. */
  onRequested: () => void
}) {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<AeEditPreview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [draft, setDraft] = useState<AeEditDraft | null>(null)
  const [reason, setReason] = useState('')
  const [validation, setValidation] = useState<Validation | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [runState, setRunState] = useState<string | null>(null)
  const keys = useRef(new Map<string, string>())
  const base = `/admin/hospitals/${hospitalId}/reports/${reportId}/ae-edit`

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const parsed = parseAeEditPreview(await fetchAPI<unknown>(`${base}/preview`))
      if (!parsed) throw new Error('preview shape')
      setPreview(parsed)
      setDraft(draftFromSummary(parsed.current))
      setValidation(null)
    } catch (caught) {
      const detail = caught instanceof ApiError && caught.status === 409 && typeof caught.detail === 'object' && caught.detail && 'message' in caught.detail
        ? String((caught.detail as { message?: unknown }).message ?? '')
        : ''
      setLoadError(detail || ADMIN_COPY.aeEditLoadFailed)
    }
  }, [base])

  useEffect(() => {
    if (open && !preview && !loadError) void load()
  }, [open, preview, loadError, load])

  useEffect(() => {
    if (!runId) return
    const timer = window.setInterval(() => {
      void fetchAPI<unknown>(`/admin/hospitals/${hospitalId}/operations/monthly-report-runs`).then((payload) => {
        const run = parseReportRuns(payload).find((item) => item.runId === runId)
        if (run && !run.isActive) {
          setRunState(run.reportVersion !== null && run.reportVersion > 0 ? 'done' : 'failed')
          setRunId(null)
          onRequested()
        }
      }).catch(() => undefined)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [hospitalId, onRequested, runId])

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className={`${BUTTON} w-full border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)] sm:w-auto`}>
        {ADMIN_COPY.aeEditTitle}
      </button>
    )
  }

  const fields = preview && draft ? buildAeEditFields(preview.original, draft, preview.overrides) : {}
  const fingerprint = aeEditFingerprint(reportId, fields)
  const validated = validation?.fingerprint === fingerprint ? validation : null
  const problems = validated ? problemsByPath(validated.problems) : {}
  const changed = hasAeEditChanges(fields)
  const canSubmit = changed && validated !== null && validated.problems.length === 0 && !busy && reason.trim().length >= 3

  function update(patch: Partial<AeEditDraft>) {
    setDraft((current) => (current ? { ...current, ...patch } : current))
    setMessage(null)
  }

  async function validate() {
    if (!preview || busy) return
    setBusy(true)
    setError(null)
    try {
      const parsed = parseAeEditPreview(await fetchAPI<unknown>(`${base}/preview?overrides=${encodeURIComponent(JSON.stringify(fields))}`))
      if (!parsed) throw new Error('preview shape')
      setValidation({ fingerprint, problems: parsed.problems })
      setMessage(parsed.problems.length === 0 ? ADMIN_COPY.aeEditPreviewOk : ADMIN_COPY.aeEditPreviewFailed)
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 409 ? ADMIN_COPY.aeEditLoadFailed : ADMIN_COPY.aeEditRequestFailed)
    } finally {
      setBusy(false)
    }
  }

  async function submit() {
    if (!canSubmit) return
    setBusy(true)
    setError(null)
    try {
      const key = getOrCreatePendingActionKey(keys.current, fingerprint, () => aeEditIdempotencyKey(reportId, crypto.randomUUID()))
      const response = await fetchAPI<{ operation_run_id?: string }>(base, {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: JSON.stringify({ reason: reason.trim(), fields }),
      })
      keys.current.delete(fingerprint)
      setMessage(ADMIN_COPY.aeEditRequested)
      setRunId(typeof response.operation_run_id === 'string' ? response.operation_run_id : null)
      setRunState('running')
      onRequested()
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 422) {
        const rejected = problemsFromRejection(caught.detail)
        setValidation({ fingerprint, problems: rejected.length > 0 ? rejected : [{ path: '', code: 'REJECTED', message: caught.message }] })
        setMessage(rejected.length > 0 ? ADMIN_COPY.aeEditPreviewFailed : caught.message)
      } else setError(ADMIN_COPY.aeEditRequestFailed)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mt-3 space-y-4">
      <p className="text-sm leading-6 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{ADMIN_COPY.aeEditHint}</p>
      <p className="rounded-lg bg-[var(--color-revisit-coolgrey-90)] p-3 text-sm leading-6 [word-break:keep-all]" role="note">{ADMIN_COPY.aeEditRedelivery}</p>
      {loadError && (
        <div className="rounded-lg border border-[var(--color-revisit-red-50)] p-3 text-sm text-[var(--color-revisit-red-50)]" role="alert">
          <p>{loadError}</p>
          <button type="button" onClick={() => void load()} className={`${BUTTON} mt-2 border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)]`}>다시 불러오기</button>
        </div>
      )}
      {!preview && !loadError && <p className="text-sm" role="status">{ADMIN_COPY.aeEditLoading}</p>}
      {preview && draft && (
        <>
          {(preview.editedBy || preview.droppedPaths.length > 0) && (
            <p className="text-xs leading-5 text-[var(--color-revisit-text-caption)] [word-break:keep-all]">
              {preview.editedBy && <>{ADMIN_COPY.aeEditEditedBy} · {preview.editedBy} · {preview.editedAt ? new Date(preview.editedAt).toLocaleString('ko-KR') : '-'}<br /></>}
              {preview.droppedPaths.length > 0 && ADMIN_COPY.aeEditDropped}
            </p>
          )}

          <fieldset className="space-y-2">
            <legend className="text-sm font-bold text-[var(--color-revisit-text-title)]">{ADMIN_COPY.aeEditKeyLines}</legend>
            {draft.keyLines.slice(0, 3).map((line, index) => (
              <Field key={index} label={['잘된 점', '보완할 점', '다음 달 중점'][index] ?? `${index + 1}번째 줄`} value={line} original={preview.original.keyLines[index] ?? ''} problems={problems[keyLinePath(index)]} onChange={(value) => update({ keyLines: draft.keyLines.map((item, i) => (i === index ? value : item)) })} />
            ))}
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="text-sm font-bold text-[var(--color-revisit-text-title)]">{ADMIN_COPY.aeEditCardNames} <span className="font-normal text-[var(--color-revisit-text-caption)]">· {preview.original.cardValue}{preview.original.cardNamesTail ? ` · ${preview.original.cardNamesTail}` : ''}</span></legend>
            <Field label="이름 목록(쉼표로 구분)" value={draft.cardNames.join(', ')} original={preview.original.cardNames.join(', ')} problems={problems[AE_CARD_NAMES_PATH]} onChange={(value) => update({ cardNames: value.split(',').map((item) => item.trim()) })} />
          </fieldset>

          <fieldset className="space-y-3">
            <legend className="text-sm font-bold text-[var(--color-revisit-text-title)]">{ADMIN_COPY.aeEditCases}</legend>
            {(['found', 'missing'] as const).map((slot) => {
              const current = preview.current.cases[slot]
              const original = preview.original.cases[slot]
              const candidates = preview.candidates[slot]
              if (!current && candidates.length === 0) return null
              return (
                <div key={slot} className="rounded-lg border border-[var(--color-revisit-coolgrey-20)] p-3">
                  <p className="text-sm font-semibold">{current?.label ?? (slot === 'found' ? '노출 사례' : '미노출 사례')}</p>
                  {current && <p className="mt-1 text-xs leading-5 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{current.keyword} · {current.platform} · {current.measuredOn}<br />{current.excerpt}</p>}
                  {candidates.length > 0 && (
                    <label className="mt-2 block text-sm font-semibold">{ADMIN_COPY.aeEditCasePick}
                      <select value={draft.caseRecords[slot] ?? ''} onChange={(event) => update({ caseRecords: { ...draft.caseRecords, [slot]: event.target.value || null } })} className={INPUT}>
                        <option value="">{ADMIN_COPY.aeEditCaseKeep}</option>
                        {candidates.map((candidate) => <option key={candidate.recordId} value={candidate.recordId}>{candidate.keyword} · {candidate.platform} · {candidate.measuredOn}</option>)}
                      </select>
                      {problems[caseRecordPath(slot)]?.map((text) => <span key={text} className="mt-1 block text-xs text-[var(--color-revisit-red-50)]" role="alert">{text}</span>)}
                    </label>
                  )}
                  {original && draft.caseInsights[slot] !== null && (
                    <Field label={ADMIN_COPY.aeEditCaseInsight} value={draft.caseInsights[slot] ?? ''} original={original.insight} problems={problems[caseInsightPath(slot)]} onChange={(value) => update({ caseInsights: { ...draft.caseInsights, [slot]: value } })} />
                  )}
                </div>
              )
            })}
          </fieldset>

          {preview.original.planRows.length > 0 && (
            <fieldset className="space-y-2">
              <legend className="text-sm font-bold text-[var(--color-revisit-text-title)]">{ADMIN_COPY.aeEditPlanRows}</legend>
              {preview.original.planRows.map((row) => (
                <Field key={row.keyword} label={`${row.priority}. ${draft.treatmentLabels[row.keyword] ?? row.treatment} · ${row.rateLabel} · ${row.keyword}${row.continued ? ' (부록 이어짐)' : ''}`} value={draft.planActions[row.keyword] ?? row.action} original={row.action} problems={problems[planActionPath(row.keyword)]} onChange={(value) => update({ planActions: { ...draft.planActions, [row.keyword]: value } })} />
              ))}
            </fieldset>
          )}

          {preview.original.keywordLabels.length > 0 && (
            <fieldset className="space-y-2">
              <legend className="text-sm font-bold text-[var(--color-revisit-text-title)]">{ADMIN_COPY.aeEditLabels}</legend>
              <p className="text-xs leading-5 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{ADMIN_COPY.aeEditLabelHint}</p>
              {preview.original.keywordLabels.map((row) => (
                <Field key={row.keyword} label={`${row.keyword} · ${row.total}`} value={draft.treatmentLabels[row.keyword] ?? row.treatment} original={row.treatment} problems={problems[treatmentLabelPath(row.keyword)]} onChange={(value) => update({ treatmentLabels: { ...draft.treatmentLabels, [row.keyword]: value } })} single />
              ))}
            </fieldset>
          )}

          <label className="block text-sm font-semibold">{ADMIN_COPY.aeEditReason}
            <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={200} placeholder="예: 진료 항목 라벨 정정" className={INPUT} />
          </label>

          {problems['']?.map((text) => <p key={text} className="text-sm text-[var(--color-revisit-red-50)]" role="alert">{text}</p>)}
          {error && <p className="text-sm text-[var(--color-revisit-red-50)]" role="alert">{error}</p>}
          {message && <p className="text-sm text-[var(--color-revisit-text-helper)]" role="status">{message}</p>}
          {runState === 'done' && <p className="text-sm text-[var(--color-revisit-green-50)]" role="status">{ADMIN_COPY.aeEditRunDone}</p>}
          {runState === 'failed' && <p className="text-sm text-[var(--color-revisit-red-50)]" role="alert">{ADMIN_COPY.aeEditRunFailed}</p>}
          <div className="flex flex-col gap-2 sm:flex-row">
            <button type="button" onClick={() => void validate()} disabled={busy || !changed} className={`${BUTTON} border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)]`}>{changed ? ADMIN_COPY.aeEditPreview : ADMIN_COPY.aeEditNoChanges}</button>
            <button type="button" onClick={() => void submit()} disabled={!canSubmit} className={`${BUTTON} bg-[var(--color-revisit-primary-40)] text-white`}>{busy ? ADMIN_COPY.aeEditSubmitting : ADMIN_COPY.aeEditSubmit}</button>
            <button type="button" onClick={() => { setOpen(false); setValidation(null) }} className={`${BUTTON} border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-helper)]`}>닫기</button>
          </div>
        </>
      )}
    </div>
  )
}

function Field({ label, value, original, problems, onChange, single = false }: { label: string; value: string; original: string; problems?: readonly string[]; onChange: (value: string) => void; single?: boolean }) {
  const changed = value.trim() !== original
  return (
    <label className="block text-sm font-semibold [word-break:keep-all]">
      <span className="flex flex-wrap items-center justify-between gap-2">{label}
        {changed && <button type="button" onClick={() => onChange(original)} className="text-xs font-bold text-[var(--color-revisit-primary-40)]">{ADMIN_COPY.aeEditRevert}</button>}
      </span>
      {single
        ? <input value={value} onChange={(event) => onChange(event.target.value)} maxLength={40} className={INPUT} />
        : <textarea value={value} onChange={(event) => onChange(event.target.value)} maxLength={200} rows={2} className={INPUT} />}
      {changed && <span className="mt-1 block text-xs font-normal text-[var(--color-revisit-text-caption)]">원문: {original}</span>}
      {problems?.map((text) => <span key={text} className="mt-1 block text-xs font-normal text-[var(--color-revisit-red-50)]" role="alert">{text}</span>)}
    </label>
  )
}
