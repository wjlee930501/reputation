'use client'

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { ApiError, fetchAPI } from '@/lib/api'
import { buildAdminCsrfHeaders } from '@/lib/csrf'
import { getOrCreatePendingActionKey } from '@/lib/pending-action-key'
import { dialogCloseAllowed } from '@/lib/report-component-behavior'
import {
  AE_DELETE,
  aeEditFingerprint,
  aeEditIdempotencyKey,
  aeEditRunOutcome,
  aeFieldId,
  buildAeEditFields,
  bulkDeleteTargets,
  bulkRestoreTargets,
  caseRecordPath,
  changedCountBySection,
  describeAeItem,
  displayValue,
  draftFromItems,
  filterAeSections,
  firstProblemTarget,
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
  type AeEditBundle,
  type AeEditDraft,
  type AeEditGroup,
  type AeEditItem,
  type AeEditPreview,
  type AeEditProblem,
  type AeEditSummary,
  type AeProblemTarget,
} from '@/lib/report-ae-edit'
import { parseReportRuns } from '@/lib/report-run'

type Validation = { readonly fingerprint: string; readonly problems: readonly AeEditProblem[]; readonly summary: AeEditSummary | null; readonly numbersPreserved: boolean }
type Recounted = { readonly value: string; readonly names: readonly string[]; readonly tail: string | null } | null

const INPUT = 'mt-1 w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white p-2 text-sm font-normal'
const BUTTON = 'min-h-11 rounded-lg px-4 text-sm font-bold disabled:opacity-40'
const BAR_BUTTON = 'min-h-9 whitespace-nowrap rounded-lg px-3 text-xs font-bold disabled:opacity-40'
const SMALL_BUTTON = 'text-xs font-bold text-[var(--color-revisit-primary-40)] disabled:opacity-40'
/** 하단 고정 바 높이만큼 스크롤 여백 — 포커스·오류 줄이 바 뒤에 숨지 않는다. 값은 바를 재어 `--ae-bar-h`로 준다. */
const SCROLL_MARGIN = '[scroll-margin-bottom:calc(var(--ae-bar-h)+16px)] [scroll-margin-top:140px]'
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
const PLACE_COPY = { bundles: ADMIN_COPY.aeEditBundles, columns: ADMIN_COPY.aeEditColumns, kinds: ADMIN_COPY.aeEditKinds, line: ADMIN_COPY.aeEditLine, column: ADMIN_COPY.aeEditColumn }

type StatusTone = 'ok' | 'warn' | 'error' | 'muted'
const TONE_CLASS: Record<StatusTone, string> = {
  ok: 'bg-[var(--color-revisit-green-20)] text-[var(--color-revisit-green-70)]',
  warn: 'bg-[var(--color-revisit-primary-95)] text-[var(--color-revisit-text-title)]',
  error: 'bg-[var(--color-revisit-red-20)] text-[var(--color-revisit-red-70)]',
  muted: 'text-[var(--color-revisit-text-caption)]',
}

/**
 * 보고서 AE 수정 판 — 원장 PDF에 찍히는 모든 글자를 보고서 순서(01~06·부록)대로 고치거나 지우고, 숫자도 바꿔 새 버전을 만든다.
 *
 * 맨 위 '문장 찾기'로 PDF에서 본 문장을 바로 찾고(맞는 절만 남고 첫 결과로 스크롤), 항목마다 '지우기'와 '원래대로', 행마다 '행 지우기',
 * 묶음(부록 D 전체·핵심 3줄 등)마다 '이 묶음 모두 지우기'가 있다. 저장 전에 서버 검증(금지 표현·내부 표식·빈 절)과 실제 PDF 미리보기
 * (패널 안)를 받고, 검증에 걸리면 '첫 문제로 이동'이 그 칸을 펼쳐 보인다. 하단 바는 한 줄이고 그 높이만큼 스크롤 여백을 둔다.
 * 저장 안 한 수정은 다이얼로그(`onDirtyChange`)가 모든 닫기 경로에서 확인한다. 새 버전은 다시 전달해야 반영된다.
 */
export function ReportAeEditPanel({
  hospitalId,
  reportId,
  onRequested,
  onDirtyChange,
  onOpenChange,
}: {
  hospitalId: string
  reportId: string
  /** 새 버전 생성을 요청한 뒤 — 목록·작업 기록을 다시 읽게 한다. */
  onRequested: () => void
  /** 저장 안 한 수정이 생기거나 없어질 때 — 다이얼로그가 X·Esc·바깥 클릭·탭 닫기를 같은 확인으로 막는다. */
  onDirtyChange?: (dirty: boolean) => void
  /** 패널을 열고 닫을 때 — 다이얼로그가 머리글을 줄인다. */
  onOpenChange?: (open: boolean) => void
}) {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<AeEditPreview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [draft, setDraft] = useState<AeEditDraft | null>(null)
  const [reason, setReason] = useState('')
  const [validation, setValidation] = useState<Validation | null>(null)
  const [busy, setBusy] = useState(false)
  const [pdfBusy, setPdfBusy] = useState(false)
  const [pdfElapsed, setPdfElapsed] = useState(0)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [runState, setRunState] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [changedOnly, setChangedOnly] = useState(false)
  const [openSections, setOpenSections] = useState<Readonly<Record<string, boolean>>>({ p1: true })
  const [focusTarget, setFocusTarget] = useState<AeProblemTarget | null>(null)
  const [barHeight, setBarHeight] = useState(0)
  const keys = useRef(new Map<string, string>())
  const barRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const pdfRef = useRef<HTMLDivElement>(null)
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

  useEffect(() => { onOpenChange?.(open) }, [open, onOpenChange])

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

  // PDF를 만드는 동안 지난 초를 센다(보통 30초쯤).
  useEffect(() => {
    if (!pdfBusy) return
    setPdfElapsed(0)
    const started = Date.now()
    const timer = window.setInterval(() => setPdfElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [pdfBusy])

  // 미리보기가 생기면 그 자리로 스크롤한다.
  useEffect(() => {
    if (pdfUrl) window.requestAnimationFrame(() => pdfRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' }))
  }, [pdfUrl])

  // 하단 고정 바의 높이를 재어 스크롤 여백(`--ae-bar-h`)으로 쓴다 — 포커스한 칸·오류 줄이 바 뒤에 숨지 않게.
  useLayoutEffect(() => {
    const bar = barRef.current
    if (!bar || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => setBarHeight(bar.getBoundingClientRect().height))
    observer.observe(bar)
    setBarHeight(bar.getBoundingClientRect().height)
    return () => observer.disconnect()
  }, [open, preview])

  // '첫 문제로 이동' — 절을 펼친 다음 프레임에 그 칸으로 스크롤·포커스한다.
  useEffect(() => {
    if (!focusTarget) return
    const frame = window.requestAnimationFrame(() => {
      const root = listRef.current
      const element = focusTarget.kind === 'field'
        ? document.getElementById(focusTarget.id)
        : focusTarget.kind === 'row'
          ? root?.querySelector<HTMLElement>(`[data-ae-group="${CSS.escape(focusTarget.id)}"]`)
          : root?.querySelector<HTMLElement>(`[data-ae-section="${CSS.escape(focusTarget.id)}"]`)
      const target = element ?? (root?.querySelector<HTMLElement>(`[data-ae-section="${CSS.escape(focusTarget.section)}"]`) ?? null)
      target?.scrollIntoView({ block: 'center' })
      if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) target.focus({ preventScroll: true })
      setFocusTarget(null)
    })
    return () => window.cancelAnimationFrame(frame)
  }, [focusTarget])

  const sections = useMemo(() => (preview ? groupAeEditItems(preview.items) : []), [preview])
  const knownPaths = useMemo(() => new Set(preview?.items.map((item) => item.path) ?? []), [preview])

  const fields = useMemo(() => (preview && draft ? buildAeEditFields(preview.items, draft, preview.overrides) : {}), [preview, draft])
  const fingerprint = aeEditFingerprint(reportId, fields)
  const validated = validation?.fingerprint === fingerprint ? validation : null
  const problems = validated ? problemsByPath(validated.problems, knownPaths) : {}
  const changed = hasAeEditChanges(fields)
  const recounted = preview && validated ? recountedCard(preview.current, validated.summary) : null
  const blocked = submitBlockedBy({ changed, validated: validated !== null, stale: validation !== null, problems: validated?.problems.length ?? 0, reasonLength: reason.trim().length })
  const canSubmit = blocked === null && !busy
  const changedCounts = preview ? changedCountBySection(preview.items, fields) : {}
  const trimmedQuery = query.trim()
  const filtering = trimmedQuery.length > 0 || changedOnly

  useEffect(() => { onDirtyChange?.(open && changed) }, [open, changed, onDirtyChange])

  const visibleItem = useCallback((item: AeEditItem): boolean => {
    if (!draft) return false
    if (changedOnly && !itemIsChanged(item, fields)) return false
    return itemMatchesQuery(item, draft.values[item.path], trimmedQuery, ADMIN_COPY.aeEditKinds[item.kind] ?? item.kind)
  }, [draft, changedOnly, trimmedQuery, fields])
  const visibleSections = useMemo(() => filterAeSections(sections, visibleItem, filtering), [sections, visibleItem, filtering])
  const matchCount = visibleSections.reduce((sum, section) => sum + section.matches, 0)

  // 검색 결과가 생기면 첫 결과로 스크롤한다(입력은 그대로 포커스).
  useEffect(() => {
    if (!trimmedQuery || matchCount === 0) return
    const timer = window.setTimeout(() => listRef.current?.querySelector<HTMLElement>('[data-ae-item]')?.scrollIntoView({ block: 'start' }), 250)
    return () => window.clearTimeout(timer)
  }, [trimmedQuery, matchCount])

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} className={`${BUTTON} w-full border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)] sm:w-auto`}>
        {ADMIN_COPY.aeEditTitle}
      </button>
    )
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

  function bulkDelete(bundle: AeEditBundle) {
    const targets = bulkDeleteTargets(bundle)
    setDraft((current) => current ? {
      ...current,
      rows: { ...current.rows, ...Object.fromEntries(targets.rows.map((row) => [row, true])) },
      values: { ...current.values, ...Object.fromEntries(targets.paths.map((path) => [path, AE_DELETE])) },
    } : current)
    setMessage(null)
  }

  function bulkRestore(bundle: AeEditBundle) {
    const targets = bulkRestoreTargets(bundle)
    setDraft((current) => current ? {
      ...current,
      rows: { ...current.rows, ...Object.fromEntries(targets.rows.map((row) => [row, false])) },
      values: { ...current.values, ...Object.fromEntries(targets.items.map((item) => [item.path, item.original])) },
    } : current)
    setMessage(null)
  }

  function close() {
    if (!dialogCloseAllowed(changed, () => window.confirm(ADMIN_COPY.aeEditDiscardConfirm))) return
    setOpen(false)
    setValidation(null)
    setPdfUrl(null)
  }

  function goToFirstProblem() {
    if (!preview || !validated) return
    const target = firstProblemTarget(validated.problems, preview.items)
    if (!target) return
    setQuery('')
    setChangedOnly(false)
    setOpenSections((current) => ({ ...current, [target.section]: true }))
    setFocusTarget(target)
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

  // 하단 바의 상태 한 줄 — 오류 > 작업 결과 > 검증 문제(N곳·첫 문제로 이동) > 검증 통과·요청 메시지 > 버튼이 꺼진 이유.
  const problemCount = validated?.problems.length ?? 0
  const otherProblems = validated ? validated.problems.filter((problem) => problem.path && !knownPaths.has(problem.path) && !problem.path.startsWith('summary.cards[3].names[')) : []
  const status: { tone: StatusTone; text: string; goFirst?: boolean } | null = error
    ? { tone: 'error', text: error }
    : runState === 'done'
      ? { tone: 'ok', text: ADMIN_COPY.aeEditRunDone }
      : runState === 'failed'
        ? { tone: 'error', text: ADMIN_COPY.aeEditRunFailed }
        : validated && problemCount > 0
          ? { tone: 'error', text: `${problemCount}${ADMIN_COPY.aeEditProblemsCount} · ${(problems['']?.[0] ?? otherProblems[0]?.message ?? ADMIN_COPY.aeEditBlockedProblems)}`, goFirst: firstProblemTarget(validated.problems, preview?.items ?? []) !== null }
          : message
            ? { tone: validated ? (validated.numbersPreserved ? 'ok' : 'warn') : 'muted', text: message }
            : blocked && !busy
              ? { tone: 'muted', text: BLOCKED_COPY[blocked] }
              : null

  return (
    <div className="mt-3 space-y-4" style={{ '--ae-bar-h': `${barHeight}px` } as CSSProperties}>
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

          {/* 찾기 상자는 고정하지 않는다 — 다이얼로그 자체의 고정 머리글과 겹쳐 열린 절 제목이 비쳐 보였다(브라우저 검증). 검색이 맞는 절만 남기고 첫 결과로 스크롤한다. */}
          <div className="space-y-2 rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white p-3">
            <label className="block text-sm font-semibold">{ADMIN_COPY.aeEditSearch}
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={ADMIN_COPY.aeEditSearchHint} className={INPUT} data-testid="ae-search" />
            </label>
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <label className="flex items-center gap-2">
                <input type="checkbox" checked={changedOnly} onChange={(event) => setChangedOnly(event.target.checked)} />
                {ADMIN_COPY.aeEditChangedOnly}
              </label>
              {filtering && <span className="text-xs text-[var(--color-revisit-text-caption)]" role="status" data-testid="ae-search-count">{matchCount > 0 ? `${matchCount}${ADMIN_COPY.aeEditSearchFound}` : ADMIN_COPY.aeEditSearchNone}</span>}
            </div>
          </div>

          <div ref={listRef} className="space-y-4">
            {visibleSections.map((section) => {
              const isOpen = filtering ? true : openSections[section.section] === true
              const count = changedCounts[section.section] ?? 0
              return (
                <details key={section.section} open={isOpen} onToggle={(event) => { if (!filtering) setOpenSections((current) => ({ ...current, [section.section]: (event.target as HTMLDetailsElement).open })) }} className={`rounded-lg border border-[var(--color-revisit-coolgrey-20)] p-3 ${SCROLL_MARGIN}`} data-ae-section={section.section}>
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
                    {problems[section.section]?.map((text) => <p key={text} className="text-xs text-[var(--color-revisit-red-50)]" role="alert">{text}</p>)}
                    {section.bundles.map((bundle) => (
                      <BundleBlock key={bundle.key} bundle={bundle} items={preview.items} draft={draft} fields={fields} problems={problems} recounted={recounted} onValue={setValue} onRow={setRow} onDeleteAll={() => bulkDelete(bundle)} onRestoreAll={() => bulkRestore(bundle)} />
                    ))}
                  </div>
                </details>
              )
            })}
            {filtering && visibleSections.length === 0 && <p className="text-xs text-[var(--color-revisit-text-caption)]">{ADMIN_COPY.aeEditSearchNone}</p>}
          </div>

          {pdfBusy && (
            <div className="space-y-2 rounded-lg border border-[var(--color-revisit-coolgrey-20)] p-3" role="status" data-testid="ae-pdf-progress">
              <p className="text-sm font-bold text-[var(--color-revisit-text-title)]">{ADMIN_COPY.aeEditPreviewPdfBusy} · {ADMIN_COPY.aeEditPdfPreviewWait} {pdfElapsed}{ADMIN_COPY.aeEditPdfPreviewElapsed}</p>
              <div className="h-2 w-full overflow-hidden rounded bg-[var(--color-revisit-coolgrey-90)]" role="progressbar" aria-label={ADMIN_COPY.aeEditPreviewPdfBusy}>
                <div className="h-full w-1/3 animate-pulse rounded bg-[var(--color-revisit-primary-40)]" />
              </div>
            </div>
          )}
          {pdfUrl && (
            <div ref={pdfRef} className={`space-y-2 ${SCROLL_MARGIN}`} data-testid="ae-pdf-preview">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-sm font-bold text-[var(--color-revisit-text-title)]">{ADMIN_COPY.aeEditPdfPreviewTitle}</p>
                <a href={pdfUrl} target="_blank" rel="noreferrer" className={SMALL_BUTTON}>{ADMIN_COPY.aeEditOpenInTab}</a>
              </div>
              {/* 높이는 화면에서 고정 바와 머리글을 뺀 만큼 — 70vh는 바 뒤로 들어갔다. */}
              <iframe title={ADMIN_COPY.aeEditPdfPreviewTitle} src={pdfUrl} className="w-full rounded-lg border border-[var(--color-revisit-coolgrey-20)]" style={{ height: 'max(320px, calc(100vh - var(--ae-bar-h) - 150px))' }} />
            </div>
          )}

          {/* 하단 고정 바 — 이유 한 칸·버튼 네 개·상태 한 줄. 좁은 폭에서도 줄바꿈만 하고 세로로 쌓지 않는다. */}
          <div ref={barRef} className="sticky bottom-0 z-10 -mx-3 -mb-3 space-y-1 border-t border-[var(--color-revisit-coolgrey-20)] bg-white px-3 pt-2 pb-2 sm:-mx-4 sm:-mb-4 sm:px-4" data-testid="ae-bar">
            <div className="flex flex-wrap items-center gap-2">
              <input value={reason} onChange={(event) => setReason(event.target.value)} maxLength={200} placeholder={ADMIN_COPY.aeEditReason} aria-label={ADMIN_COPY.aeEditReason} className="min-h-9 min-w-[10rem] flex-1 rounded-lg border border-[var(--color-revisit-coolgrey-20)] bg-white px-2 text-sm" data-testid="ae-reason" />
              <button type="button" onClick={() => void validate()} disabled={busy || !changed} className={`${BAR_BUTTON} border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)]`}>{ADMIN_COPY.aeEditPreview}</button>
              <button type="button" onClick={() => void previewPdf()} disabled={pdfBusy || busy} className={`${BAR_BUTTON} border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-title)]`}>{pdfBusy ? ADMIN_COPY.aeEditPreviewPdfBusy : ADMIN_COPY.aeEditPreviewPdf}</button>
              <button type="button" onClick={() => void submit()} disabled={!canSubmit} className={`${BAR_BUTTON} bg-[var(--color-revisit-primary-40)] text-white`}>{busy ? ADMIN_COPY.aeEditSubmitting : ADMIN_COPY.aeEditSubmit}</button>
              <button type="button" onClick={close} className={`${BAR_BUTTON} border border-[var(--color-revisit-coolgrey-20)] bg-white text-[var(--color-revisit-text-helper)]`}>{ADMIN_COPY.aeEditClose}</button>
            </div>
            {status && (
              <p className={`flex flex-wrap items-center gap-2 rounded px-2 py-1 text-xs leading-5 [word-break:keep-all] ${TONE_CLASS[status.tone]}`} role={status.tone === 'error' ? 'alert' : 'status'} data-testid={status.tone === 'muted' ? 'ae-submit-blocked' : 'ae-status'} data-tone={status.tone}>
                {status.tone === 'ok' && <svg aria-hidden="true" viewBox="0 0 20 20" className="h-4 w-4 shrink-0" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M4 10.5l4 4 8-9" /></svg>}
                <span>{status.text}</span>
                {status.goFirst && <button type="button" onClick={goToFirstProblem} className="rounded border border-current px-2 py-0.5 text-xs font-bold" data-testid="ae-go-first-problem">{ADMIN_COPY.aeEditGoFirstProblem}</button>}
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}

/** 묶음 — 연속된 같은 종류의 행·줄(부록 D 유의사항 전체, 핵심 3줄, 콘텐츠 목록 …). 지울 대상이 둘 이상이면 '이 묶음 모두 지우기 / 모두 원래대로'가 있다. */
function BundleBlock({ bundle, items, draft, fields, problems, recounted, onValue, onRow, onDeleteAll, onRestoreAll }: {
  bundle: AeEditBundle
  items: readonly AeEditItem[]
  draft: AeEditDraft
  fields: Readonly<Record<string, unknown>>
  problems: Readonly<Record<string, string[]>>
  recounted: Recounted
  onValue: (path: string, value: unknown) => void
  onRow: (row: string, deleted: boolean) => void
  onDeleteAll: () => void
  onRestoreAll: () => void
}) {
  const name = ADMIN_COPY.aeEditBundles[bundle.key]
  const anyChanged = bundle.groups.some((group) => group.items.some((item) => itemIsChanged(item, fields)))
  const targets = bulkDeleteTargets(bundle)
  const allDeleted = targets.rows.every((row) => draft.rows[row] === true) && targets.paths.every((path) => isDeleteMarker(draft.values[path]))
  return (
    <div className="space-y-2" data-ae-bundle={bundle.key}>
      {bundle.bulk && name && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--color-revisit-coolgrey-20)] pb-1">
          <p className="text-xs font-bold text-[var(--color-revisit-text-title)]">{name}</p>
          <span className="flex gap-3">
            {!allDeleted && <button type="button" onClick={onDeleteAll} className={SMALL_BUTTON} data-testid="ae-bundle-delete">{ADMIN_COPY.aeEditBundleDeleteAll}</button>}
            {anyChanged && <button type="button" onClick={onRestoreAll} className={SMALL_BUTTON} data-testid="ae-bundle-restore">{ADMIN_COPY.aeEditBundleRestoreAll}</button>}
          </span>
        </div>
      )}
      {bundle.groups.map((group) => (
        <GroupBlock key={group.key} group={group} items={items} draft={draft} problems={problems} recounted={recounted} onValue={onValue} onRow={onRow} />
      ))}
    </div>
  )
}

/** 행 묶음(표의 한 행·카드 하나·사례 하나·검정 면 하나) 또는 항목 하나. 머리에는 PDF에서의 위치가, 지울 수 있는 행이면 '행 지우기 / 행 원래대로'가 있다. */
function GroupBlock({ group, items, draft, problems, recounted, onValue, onRow }: {
  group: AeEditGroup
  items: readonly AeEditItem[]
  draft: AeEditDraft
  problems: Readonly<Record<string, string[]>>
  recounted: Recounted
  onValue: (path: string, value: unknown) => void
  onRow: (row: string, deleted: boolean) => void
}) {
  const rowDeleted = group.row !== null && draft.rows[group.row] === true
  const place = describeAeItem(group.items[0], items, draft.values, PLACE_COPY).place
  return (
    <div className={`rounded-lg border p-2 ${SCROLL_MARGIN} ${rowDeleted ? 'border-dashed border-[var(--color-revisit-red-50)] bg-[var(--color-revisit-coolgrey-90)] opacity-70' : 'border-[var(--color-revisit-coolgrey-20)]'}`} data-ae-group={group.key}>
      {(place || group.rowDeletable) && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs font-bold text-[var(--color-revisit-text-caption)] [word-break:keep-all]" data-testid="ae-group-place">{place}{rowDeleted ? ` · ${ADMIN_COPY.aeEditRowDeleted}` : ''}</p>
          {group.rowDeletable && <button type="button" onClick={() => onRow(group.row as string, !rowDeleted)} className={SMALL_BUTTON}>{rowDeleted ? ADMIN_COPY.aeEditRestoreRow : ADMIN_COPY.aeEditDeleteRow}</button>}
        </div>
      )}
      {problems[group.row ?? '']?.map((text) => group.row && <span key={text} className="mt-1 block text-xs text-[var(--color-revisit-red-50)]" role="alert">{text}</span>)}
      {group.items.map((item) => (
        <ItemField key={item.path} item={item} name={describeAeItem(item, items, draft.values, PLACE_COPY).name} value={draft.values[item.path]} problems={problems[item.path]} recounted={item.path === 'summary.cards[3].names' ? recounted : null} onChange={(value) => onValue(item.path, value)} />
      ))}
    </div>
  )
}

/** 내용 높이에 맞춰 늘어나는 글 상자 — rows=2 고정으로 긴 문장이 잘리고 상자 안 스크롤이 생겼다. */
function AutoTextarea({ id, value, onChange, maxLength }: { id: string; value: string; onChange: (value: string) => void; maxLength: number }) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }, [value])
  return <textarea ref={ref} id={id} value={value} onChange={(event) => onChange(event.target.value)} maxLength={maxLength} rows={1} className={`${INPUT} resize-none overflow-hidden`} data-ae-item="" />
}

/** 입력란 하나 — 종류에 따라 글자·수·상태·목록. '지우기'는 `AE_DELETE`, '원래대로'는 원문 값으로 되돌린다. 지운 항목은 원문이 취소선으로 남는다. */
function ItemField({ item, name, value, problems, recounted, onChange }: {
  item: AeEditItem
  name: string
  value: unknown
  problems?: readonly string[]
  recounted: Recounted
  onChange: (value: unknown) => void
}) {
  const deleted = isDeleteMarker(value)
  const shown = displayValue(item, value)
  const original = displayValue(item, item.original)
  const changed = deleted || shown !== original
  const differs = !deleted && numbersDiffer(item, value)
  const id = aeFieldId(item.path)
  const numeric = item.type === 'count' || item.type === 'pct' || item.type === 'pct_opt' || item.type === 'int'
  return (
    <div className={`mt-2 text-sm font-semibold [word-break:keep-all] ${SCROLL_MARGIN} ${deleted ? 'rounded bg-[var(--color-revisit-coolgrey-90)] p-2' : ''}`} data-ae-field={item.path}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label htmlFor={id}>{name}
          {deleted && <span className="ml-2 rounded bg-[var(--color-revisit-red-20)] px-1 text-xs text-[var(--color-revisit-red-70)]">{ADMIN_COPY.aeEditDeleted}</span>}
          {differs && <span className="ml-2 rounded bg-[var(--color-revisit-red-50)] px-1 text-xs text-white" data-testid="ae-numbers-differ">{ADMIN_COPY.aeEditNumbersDiffer}</span>}
        </label>
        <span className="flex gap-3">
          {item.deletable && !deleted && <button type="button" onClick={() => onChange(AE_DELETE)} className={SMALL_BUTTON}>{ADMIN_COPY.aeEditDelete}</button>}
          {changed && <button type="button" onClick={() => onChange(item.original)} className={SMALL_BUTTON}>{ADMIN_COPY.aeEditRevert}</button>}
        </span>
      </div>
      {deleted ? (
        <p className="mt-1 text-sm font-normal leading-6 text-[var(--color-revisit-text-caption)] line-through" data-testid="ae-deleted-original">{original || '—'}</p>
      ) : item.type === 'state' ? (
        <select id={id} value={shown} onChange={(event) => onChange(event.target.value)} className={INPUT} data-ae-item="">
          {STATE_OPTIONS.map(([state, text]) => <option key={state} value={state}>{text}</option>)}
        </select>
      ) : item.type === 'text' ? (
        <AutoTextarea id={id} value={shown} onChange={onChange} maxLength={400} />
      ) : (
        <input id={id} value={shown} onChange={(event) => onChange(event.target.value)} maxLength={item.type === 'label' ? 60 : 200} inputMode={numeric ? 'decimal' : 'text'} className={INPUT} data-ae-item="" />
      )}
      {recounted && (
        <p className="mt-1 text-xs font-normal leading-5 text-[var(--color-revisit-text-caption)]" role="status" data-testid="ae-card-recounted">
          {ADMIN_COPY.aeEditCardRecounted} · {recounted.value} · {recounted.names.join(', ')}{recounted.tail ? ` · ${recounted.tail}` : ''}
        </p>
      )}
      {changed && !deleted && <span className="mt-1 block text-xs font-normal text-[var(--color-revisit-text-caption)]">{ADMIN_COPY.aeEditOriginal}: {original || '—'}</span>}
      {problems?.map((text) => <span key={text} className="mt-1 block text-xs font-normal text-[var(--color-revisit-red-50)]" role="alert">{text}</span>)}
    </div>
  )
}
