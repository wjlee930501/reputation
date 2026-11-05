'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { ApiError, fetchAPI } from '@/lib/api'
import { buildAdminCsrfHeaders } from '@/lib/csrf'
import { getOrCreatePendingActionKey } from '@/lib/pending-action-key'
import {
  AE_DELETE,
  aeEditFingerprint,
  aeEditIdempotencyKey,
  aeEditRunOutcome,
  buildAeEditFields,
  caseRecordPath,
  changedCountBySection,
  displayValue,
  draftFromItems,
  groupAeEditItems,
  hasAeEditChanges,
  isDeleteMarker,
  itemIsChanged,
  itemMatchesQuery,
  numbersDiffer,
  parseAeEditPreview,
  problemsByPath,
  problemsFromRejection,
  recountedCard,
  submitBlockedBy,
  type AeEditDraft,
  type AeEditGroup,
  type AeEditItem,
  type AeEditPreview,
  type AeEditProblem,
  type AeEditSummary,
} from '@/lib/report-ae-edit'
import { parseReportRuns } from '@/lib/report-run'

type Validation = { readonly fingerprint: string; readonly problems: readonly AeEditProblem[]; readonly summary: AeEditSummary | null; readonly numbersPreserved: boolean }

const INPUT = 'mt-1 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white p-2 text-sm font-normal'
const BUTTON = 'min-h-11 rounded-lg px-4 text-sm font-bold disabled:opacity-40'
const SMALL_BUTTON = 'text-xs font-bold text-[var(--color-revisit-primary-40)] disabled:opacity-40'
const STATE_OPTIONS = [
  ['mentioned', ADMIN_COPY.aeEditStateMentioned],
  ['unmentioned', ADMIN_COPY.aeEditStateUnmentioned],
  ['failed', ADMIN_COPY.aeEditStateFailed],
  ['unmeasured', ADMIN_COPY.aeEditStateUnmeasured],
] as const
const BLOCKED_COPY = {
  no_changes: ADMIN_COPY.aeEditBlockedNoChanges,
  validate_first: ADMIN_COPY.aeEditBlockedValidateFirst,
  revalidate: ADMIN_COPY.aeEditBlockedRevalidate,
  problems: ADMIN_COPY.aeEditBlockedProblems,
  reason: ADMIN_COPY.aeEditBlockedReason,
} as const

/**
 * 보고서 AE 수정 판 — 원장 PDF에 찍히는 모든 글자를 보고서 순서(01~06·부록)대로 고치거나 지우고, 숫자도 바꿔 새 버전을 만든다.
 *
 * 맨 위 '문장 찾기'로 PDF에서 본 문장을 바로 찾고, 항목마다 '지우기'와 '원래대로', 행마다 '행 지우기'가 있다. 저장 전에 서버 검증
 * (금지 표현·내부 표식·빈 절)과 실제 PDF 미리보기(패널 안)를 받는다. 새 버전은 다시 전달해야 반영된다.
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
  const [pdfBusy, setPdfBusy] = useState(false)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [runState, setRunState] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [changedOnly, setChangedOnly] = useState(false)
  const [openSections, setOpenSections] = useState<Readonly<Record<string, boolean>>>({ p1: true })
  const keys = useRef(new Map<string, string>())
  const base = `/admin/hospitals/${hospitalId}/reports/${reportId}/ae-edit`

  const load = useCallback(async () => {
    setLoadError(null)
    try {
      const parsed = parseAeEditPreview(await fetchAPI<unknown>(`${base}/preview`))
      if (!parsed) throw new Error('preview shape')
      setPreview(parsed)
      setDraft(draftFromItems(parsed.items))
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
        const outcome = aeEditRunOutcome(parseReportRuns(payload).find((item) => item.runId === runId), preview?.reportVersion ?? 0)
        if (outcome !== 'running') {
          setRunState(outcome)
          setRunId(null)
          onRequested()
        }
      }).catch(() => undefined)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [hospitalId, onRequested, preview?.reportVersion, runId])

  // 미리보기 PDF의 blob URL은 다음 미리보기나 패널을 닫을 때 해제한다.
  useEffect(() => () => { if (pdfUrl) URL.revokeObjectURL(pdfUrl) }, [pdfUrl])

  const sections = useMemo(() => (preview ? groupAeEditItems(preview.items) : []), [preview])
  const knownPaths = useMemo(() => new Set(preview?.items.map((item) => item.path) ?? []), [preview])

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className={`${BUTTON} w-full border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)] sm:w-auto`}>
        {ADMIN_COPY.aeEditTitle}
      </button>
    )
  }

  const fields = preview && draft ? buildAeEditFields(preview.items, draft, preview.overrides) : {}
  const fingerprint = aeEditFingerprint(reportId, fields)
  const validated = validation?.fingerprint === fingerprint ? validation : null
  const problems = validated ? problemsByPath(validated.problems, knownPaths) : {}
  const changed = hasAeEditChanges(fields)
  const recounted = preview && validated ? recountedCard(preview.current, validated.summary) : null
  const blocked = submitBlockedBy({ changed, validated: validated !== null, stale: validation !== null, problems: validated?.problems.length ?? 0, reasonLength: reason.trim().length })
  const canSubmit = blocked === null && !busy
  const changedCounts = preview ? changedCountBySection(preview.items, fields) : {}
  const trimmedQuery = query.trim()

  function visibleItem(item: AeEditItem): boolean {
    if (!draft) return false
    if (changedOnly && !itemIsChanged(item, fields)) return false
    return itemMatchesQuery(item, draft.values[item.path], trimmedQuery, ADMIN_COPY.aeEditKinds[item.kind] ?? item.kind)
  }

  function setValue(path: string, value: unknown) {
    setDraft((current) => (current ? { ...current, values: { ...current.values, [path]: value } } : current))
    setMessage(null)
  }

  function setRow(row: string, deleted: boolean) {
    setDraft((current) => (current ? { ...current, rows: { ...current.rows, [row]: deleted } } : current))
    setMessage(null)
  }

  function setCase(slot: 'found' | 'missing', choice: string) {
    setDraft((current) => (current ? { ...current, caseRecords: { ...current.caseRecords, [slot]: choice } } : current))
    setMessage(null)
  }

  function close() {
    if (changed && !window.confirm(ADMIN_COPY.aeEditDiscardConfirm)) return
    setOpen(false)
    setValidation(null)
    setPdfUrl(null)
  }

  async function validate() {
    if (!preview || busy) return
    setBusy(true)
    setError(null)
    try {
      const parsed = parseAeEditPreview(await fetchAPI<unknown>(`${base}/preview`, { method: 'POST', body: JSON.stringify({ fields }) }))
      if (!parsed) throw new Error('preview shape')
      setValidation({ fingerprint, problems: parsed.problems, summary: parsed.preview, numbersPreserved: parsed.numbersPreserved })
      setMessage(parsed.problems.length > 0 ? ADMIN_COPY.aeEditPreviewFailed : parsed.numbersPreserved ? ADMIN_COPY.aeEditPreviewOk : ADMIN_COPY.aeEditPreviewOkNumbers)
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 409 ? ADMIN_COPY.aeEditLoadFailed : ADMIN_COPY.aeEditRequestFailed)
    } finally {
      setBusy(false)
    }
  }

  async function previewPdf() {
    if (!preview || pdfBusy) return
    setPdfBusy(true)
    setError(null)
    try {
      const response = await fetch(`/api${base}/preview-pdf`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', ...buildAdminCsrfHeaders('POST') },
        body: JSON.stringify({ fields }),
      })
      if (!response.ok) {
        let detail: unknown = null
        try { detail = (await response.json()).detail } catch { detail = null }
        const rejected = problemsFromRejection(detail)
        setValidation({ fingerprint, problems: rejected.length > 0 ? rejected : [{ path: '', code: 'PDF_PREVIEW_FAILED', message: ADMIN_COPY.aeEditPreviewPdfFailed }], summary: null, numbersPreserved: true })
        setMessage(ADMIN_COPY.aeEditPreviewPdfFailed)
        return
      }
      // 서버가 같은 수정 값을 검증하고 PDF까지 만들었다 — 앞서 남은 미리보기 실패 상태를 지우고 패널 안에 띄운다(새 탭은 팝업 차단을 받는다).
      setValidation({ fingerprint, problems: [], summary: null, numbersPreserved: true })
      setMessage(null)
      setPdfUrl(URL.createObjectURL(await response.blob()))
    } catch {
      setError(ADMIN_COPY.aeEditRequestFailed)
    } finally {
      setPdfBusy(false)
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
        setValidation({ fingerprint, problems: rejected.length > 0 ? rejected : [{ path: '', code: 'REJECTED', message: caught.message }], summary: null, numbersPreserved: true })
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
          <button type="button" onClick={() => void load()} className={`${BUTTON} mt-2 border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)]`}>{ADMIN_COPY.aeEditReload}</button>
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

          {/* 찾기 상자는 고정하지 않는다 — 다이얼로그 자체의 고정 머리글과 겹쳐 열린 절 제목이 비쳐 보였다(브라우저 검증). 검색이 맞는 절을 펼치므로 위로 올라올 일이 적다. */}
          <div className="space-y-2 rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white p-3">
            <label className="block text-sm font-semibold">{ADMIN_COPY.aeEditSearch}
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={ADMIN_COPY.aeEditSearchHint} className={INPUT} data-testid="ae-search" />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={changedOnly} onChange={(event) => setChangedOnly(event.target.checked)} />
              {ADMIN_COPY.aeEditChangedOnly}
            </label>
          </div>

          {sections.map((section) => {
            const groups = section.groups.map((group) => ({ ...group, items: group.items.filter(visibleItem) })).filter((group) => group.items.length > 0)
            const filtering = trimmedQuery.length > 0 || changedOnly
            const isOpen = filtering ? groups.length > 0 : openSections[section.section] === true
            const count = changedCounts[section.section] ?? 0
            return (
              <details key={section.section} open={isOpen} onToggle={(event) => { if (!filtering) setOpenSections((current) => ({ ...current, [section.section]: (event.target as HTMLDetailsElement).open })) }} className="rounded-lg border border-[var(--color-revisit-coolgrey-20)] p-3" data-ae-section={section.section}>
                <summary className="cursor-pointer text-sm font-bold text-[var(--color-revisit-text-title)]">
                  {ADMIN_COPY.aeEditSections[section.section] ?? section.section}
                  {count > 0 && <span className="ml-2 rounded bg-[var(--color-revisit-primary-40)] px-1.5 text-xs text-white" data-testid="ae-section-count">{count}{ADMIN_COPY.aeEditChangedCount}</span>}
                </summary>
                <div className="mt-2 space-y-3">
                  {section.section === 'p5' && !filtering && (
                    <fieldset className="space-y-3">
                      <legend className="text-sm font-bold text-[var(--color-revisit-text-title)]">{ADMIN_COPY.aeEditCases}</legend>
                      {(['found', 'missing'] as const).map((slot) => {
                        const current = preview.current.cases[slot]
                        const candidates = preview.candidates[slot]
                        if (!current && candidates.length === 0) return null
                        return (
                          <div key={slot} className="rounded-lg border border-[var(--color-revisit-coolgrey-20)] p-3">
                            <p className="text-sm font-semibold">{current?.label ?? (slot === 'found' ? ADMIN_COPY.aeEditCaseFound : ADMIN_COPY.aeEditCaseMissing)}</p>
                            {current && <p className="mt-1 text-xs leading-5 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{current.keyword} · {current.platform} · {current.measuredOn}</p>}
                            {candidates.length > 0 && (
                              <label className="mt-2 block text-sm font-semibold">{ADMIN_COPY.aeEditCasePick}
                                <select value={draft.caseRecords[slot]} onChange={(event) => setCase(slot, event.target.value)} className={INPUT}>
                                  <option value="keep">{ADMIN_COPY.aeEditCaseKeep}</option>
                                  {caseRecordPath(slot) in preview.overrides && <option value="revert">{ADMIN_COPY.aeEditCaseRevert}</option>}
                                  {candidates.map((candidate) => <option key={candidate.recordId} value={candidate.recordId}>{candidate.keyword} · {candidate.platform} · {candidate.measuredOn}</option>)}
                                </select>
                                {problems[caseRecordPath(slot)]?.map((text) => <span key={text} className="mt-1 block text-xs text-[var(--color-revisit-red-50)]" role="alert">{text}</span>)}
                              </label>
                            )}
                          </div>
                        )
                      })}
                    </fieldset>
                  )}
                  {section.section === 'appendix' && !filtering && <p className="text-xs leading-5 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">{ADMIN_COPY.aeEditLabelHint}</p>}
                  {groups.length === 0 && <p className="text-xs text-[var(--color-revisit-text-caption)]">{ADMIN_COPY.aeEditSearchNone}</p>}
                  {groups.map((group) => (
                    <GroupBlock key={group.key} group={group} draft={draft} problems={problems} recounted={recounted} onValue={setValue} onRow={setRow} />
                  ))}
                </div>
              </details>
            )
          })}

          {pdfUrl && (
            <div className="space-y-2" data-testid="ae-pdf-preview">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-bold text-[var(--color-revisit-text-title)]">{ADMIN_COPY.aeEditPdfPreviewTitle}</p>
                <a href={pdfUrl} target="_blank" rel="noreferrer" className={SMALL_BUTTON}>{ADMIN_COPY.aeEditOpenInTab}</a>
              </div>
              <iframe title={ADMIN_COPY.aeEditPdfPreviewTitle} src={pdfUrl} className="h-[70vh] w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)]" />
            </div>
          )}

          <div className="sticky bottom-0 z-10 space-y-2 border-t border-[var(--color-revisit-coolgrey-20)] bg-white pt-3 pb-2">
            <label className="block text-sm font-semibold">{ADMIN_COPY.aeEditReason}
              <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={200} placeholder={ADMIN_COPY.aeEditReasonPlaceholder} className={INPUT} />
            </label>
            {problems['']?.map((text) => <p key={text} className="text-sm text-[var(--color-revisit-red-50)]" role="alert">{text}</p>)}
            {validated && validated.problems.filter((problem) => problem.path && !knownPaths.has(problem.path) && !problem.path.startsWith('summary.cards[3].names[')).map((problem) => (
              <p key={`${problem.path}:${problem.message}`} className="text-sm text-[var(--color-revisit-red-50)]" role="alert">{ADMIN_COPY.aeEditSections[problem.path] ?? problem.path}: {problem.message}</p>
            ))}
            {error && <p className="text-sm text-[var(--color-revisit-red-50)]" role="alert">{error}</p>}
            {message && <p className="text-sm text-[var(--color-revisit-text-helper)]" role="status">{message}</p>}
            {runState === 'done' && <p className="text-sm text-[var(--color-revisit-green-50)]" role="status">{ADMIN_COPY.aeEditRunDone}</p>}
            {runState === 'failed' && <p className="text-sm text-[var(--color-revisit-red-50)]" role="alert">{ADMIN_COPY.aeEditRunFailed}</p>}
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
              <button type="button" onClick={() => void validate()} disabled={busy || !changed} className={`${BUTTON} border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)]`}>{ADMIN_COPY.aeEditPreview}</button>
              <button type="button" onClick={() => void previewPdf()} disabled={pdfBusy || busy} className={`${BUTTON} border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)]`}>{pdfBusy ? ADMIN_COPY.aeEditPreviewPdfBusy : ADMIN_COPY.aeEditPreviewPdf}</button>
              <button type="button" onClick={() => void submit()} disabled={!canSubmit} className={`${BUTTON} bg-[var(--color-revisit-primary-40)] text-white`}>{busy ? ADMIN_COPY.aeEditSubmitting : ADMIN_COPY.aeEditSubmit}</button>
              <button type="button" onClick={close} className={`${BUTTON} border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-helper)]`}>{ADMIN_COPY.aeEditClose}</button>
              {blocked && !busy && <span className="text-xs text-[var(--color-revisit-text-caption)] [word-break:keep-all]" data-testid="ae-submit-blocked">{BLOCKED_COPY[blocked]}</span>}
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/** 행 묶음(표의 한 행·카드 하나·사례 하나·검정 면 하나) 또는 항목 하나. 지울 수 있는 행이면 '행 지우기 / 행 원래대로'가 있다. */
function GroupBlock({ group, draft, problems, recounted, onValue, onRow }: {
  group: AeEditGroup
  draft: AeEditDraft
  problems: Readonly<Record<string, string[]>>
  recounted: { readonly value: string; readonly names: readonly string[]; readonly tail: string | null } | null
  onValue: (path: string, value: unknown) => void
  onRow: (row: string, deleted: boolean) => void
}) {
  const rowDeleted = group.row !== null && draft.rows[group.row] === true
  const isRow = group.row !== null && group.items.length > 1
  return (
    <div className={`rounded-lg border p-2 ${rowDeleted ? 'border-dashed border-[var(--color-revisit-red-50)] opacity-70' : 'border-[var(--color-revisit-coolgrey-20)]'}`} data-ae-group={group.key}>
      {(isRow || group.rowDeletable) && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-bold text-[var(--color-revisit-text-caption)] [word-break:keep-all]">{group.context}{rowDeleted ? ` · ${ADMIN_COPY.aeEditRowDeleted}` : ''}</p>
          {group.rowDeletable && <button type="button" onClick={() => onRow(group.row as string, !rowDeleted)} className={SMALL_BUTTON}>{rowDeleted ? ADMIN_COPY.aeEditRestoreRow : ADMIN_COPY.aeEditDeleteRow}</button>}
        </div>
      )}
      {problems[group.row ?? '']?.map((text) => group.row && <span key={text} className="mt-1 block text-xs text-[var(--color-revisit-red-50)]" role="alert">{text}</span>)}
      {group.items.map((item) => (
        <ItemField key={item.path} item={item} value={draft.values[item.path]} problems={problems[item.path]} recounted={item.path === 'summary.cards[3].names' ? recounted : null} onChange={(value) => onValue(item.path, value)} />
      ))}
    </div>
  )
}

/** 입력란 하나 — 종류에 따라 글자·수·상태·목록. '지우기'는 `AE_DELETE`, '원래대로'는 원문 값으로 되돌린다. */
function ItemField({ item, value, problems, recounted, onChange }: {
  item: AeEditItem
  value: unknown
  problems?: readonly string[]
  recounted: { readonly value: string; readonly names: readonly string[]; readonly tail: string | null } | null
  onChange: (value: unknown) => void
}) {
  const deleted = isDeleteMarker(value)
  const shown = displayValue(item, value)
  const original = displayValue(item, item.original)
  const changed = deleted || shown !== original
  const differs = !deleted && numbersDiffer(item, value)
  const id = `ae-${encodeURIComponent(item.path)}`
  const label = ADMIN_COPY.aeEditKinds[item.kind] ?? item.kind
  const numeric = item.type === 'count' || item.type === 'pct' || item.type === 'pct_opt' || item.type === 'int'
  return (
    <div className="mt-2 text-sm font-semibold [word-break:keep-all]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label htmlFor={id}>{label}{item.context && !item.row ? <span className="font-normal text-[var(--color-revisit-text-caption)]"> · {item.context}</span> : null}
          {deleted && <span className="ml-2 rounded bg-[var(--color-revisit-coolgrey-90)] px-1 text-xs">{ADMIN_COPY.aeEditDeleted}</span>}
          {differs && <span className="ml-2 rounded bg-[var(--color-revisit-red-50)] px-1 text-xs text-white" data-testid="ae-numbers-differ">{ADMIN_COPY.aeEditNumbersDiffer}</span>}
        </label>
        <span className="flex gap-3">
          {item.deletable && !deleted && <button type="button" onClick={() => onChange(AE_DELETE)} className={SMALL_BUTTON}>{ADMIN_COPY.aeEditDelete}</button>}
          {changed && <button type="button" onClick={() => onChange(item.original)} className={SMALL_BUTTON}>{ADMIN_COPY.aeEditRevert}</button>}
        </span>
      </div>
      {deleted ? null : item.type === 'state' ? (
        <select id={id} value={shown} onChange={(event) => onChange(event.target.value)} className={INPUT}>
          {STATE_OPTIONS.map(([state, text]) => <option key={state} value={state}>{text}</option>)}
        </select>
      ) : item.type === 'text' ? (
        <textarea id={id} value={shown} onChange={(event) => onChange(event.target.value)} maxLength={400} rows={2} className={INPUT} />
      ) : (
        <input id={id} value={shown} onChange={(event) => onChange(event.target.value)} maxLength={item.type === 'label' ? 60 : 200} inputMode={numeric ? 'decimal' : 'text'} className={INPUT} />
      )}
      {recounted && (
        <p className="mt-1 text-xs font-normal leading-5 text-[var(--color-revisit-text-caption)]" role="status" data-testid="ae-card-recounted">
          {ADMIN_COPY.aeEditCardRecounted} · {recounted.value} · {recounted.names.join(', ')}{recounted.tail ? ` · ${recounted.tail}` : ''}
        </p>
      )}
      {changed && <span className="mt-1 block text-xs font-normal text-[var(--color-revisit-text-caption)]">{ADMIN_COPY.aeEditOriginal}: {original || '—'}</span>}
      {problems?.map((text) => <span key={text} className="mt-1 block text-xs font-normal text-[var(--color-revisit-red-50)]" role="alert">{text}</span>)}
    </div>
  )
}
