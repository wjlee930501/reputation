'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useParams } from 'next/navigation'
import Image from 'next/image'
import ReactMarkdown from 'react-markdown'
import { ApiError, fetchAPI } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import {
  belongsToMonthView,
  buildPublicContentUrl,
  getPublishNotificationPresentation,
  sortCarriedOverFirst,
} from '@/lib/content'
import {
  ContentRowFilter,
  RowTone,
  blockedHint,
  canConfirmSample,
  describeRowState,
  matchesRowFilter,
  summarizeRows,
} from '@/lib/content-rows'
import { hospitalOperationsHref } from '@/lib/operations-center'
import {
  ContentDraftSnapshot,
  clearDraftSnapshot,
  draftDiffersFromCurrent,
  editFieldsDiffer,
  readDraftSnapshot,
  saveDraftSnapshot,
} from '@/lib/edit-draft-snapshot'
import { formatDate, formatDateTime } from '@/lib/format'
import { platformSubdomainHost } from '@/lib/platform-domain'
import { fetchCurrentAccount } from '@/lib/current-account'
import { resolveAuditActorName } from '@/lib/publishing'
import { ContentItem, ContentReference, TYPE_LABELS } from '@/types'
import { useHospitalHeader } from '../hospital-context'
import { ReadOnlySignals } from './ReadOnlySignals'
import { ScheduleSection } from './ScheduleSection'

const ESSENCE_LABELS: Record<string, { label: string; color: string }> = {
  ALIGNED: { label: '운영 기준 통과', color: 'bg-green-100 text-green-700' },
  NEEDS_ESSENCE_REVIEW: { label: '운영 기준 재검토', color: 'bg-orange-100 text-orange-700' },
  MISSING_APPROVED_PHILOSOPHY: { label: '운영 기준 없음', color: 'bg-red-100 text-red-700' },
}

const ESSENCE_FALLBACK = { label: '미검수', color: 'bg-slate-100 text-slate-500' }

// 행 상태의 톤 하나로 배지 색을 정한다 — 화면이 상태별 색을 따로 짓지 않는다.
const ROW_TONE_BADGES: Record<RowTone, string> = {
  good: 'bg-green-100 text-green-700',
  neutral: 'bg-slate-100 text-slate-600',
  warn: 'bg-amber-100 text-amber-800',
  paused: 'bg-slate-100 text-slate-500',
}

const EDIT_SAVE_NOTICE = '저장하면 자동 안전검사·재검수, 공개 글은 이미지 재인증까지 자동으로 진행됩니다.'

// 프론트엔드 미리보기용 금지 표현 목록 — backend/app/utils/medical_filter.py의
// FORBIDDEN_PATTERNS와 정규식이 동기화됨. 단순 포함 검사 대신 정규식으로 변형까지 포착.
interface ForbiddenRule {
  label: string
  pattern: RegExp
}

const FORBIDDEN_RULES: ForbiddenRule[] = [
  { label: '1등', pattern: /1등|일등|1위|일위/ },
  { label: '최고', pattern: /최고[의]?|최상[의]?|으뜸[인]?/ },
  { label: '최우수', pattern: /최우수|가장\s*우수|제일\s*우수|탁월[한]?/ },
  { label: '유일', pattern: /유일[한]?|유일무이|전국\s*유일|오직\s*이곳/ },
  { label: '완치', pattern: /완치[율]?|완전\s*치료|완전\s*회복/ },
  { label: '100%', pattern: /100\s*%|백\s*퍼센트|100퍼/ },
  { label: '성공률', pattern: /성공률|성공\s*확률|성공\s*보장/ },
  { label: '부작용 없는', pattern: /부작용\s*(없|zero|제로|걱정\s*없)/ },
  { label: '검증된', pattern: /검증[된]?|입증[된]?|확인[된]\s*효과/ },
  { label: '가장 잘하는', pattern: /가장\s*(잘|뛰어|훌륭)|제일\s*(잘|뛰어)/ },
  { label: '국내 최초', pattern: /(국내|세계|아시아|전국)\s*최초/ },
  { label: '특허', pattern: /특허[를]?\s*(보유|획득|취득|출원|등록)/ },
  { label: '독보적', pattern: /독보적[인]?|비교\s*불가/ },
  { label: '노하우', pattern: /(저희|우리|병원|원장)[\w가-힣]*\s*만[의]?\s*노하우|차별화된\s*노하우/ },
  { label: '효과 보장', pattern: /효과[를]?\s*(보장|확실|약속)|보장[된]?\s*효과/ },
  { label: '최첨단', pattern: /최첨단|첨단[의]?\s*(기술|장비|시술)/ },
  { label: '안전한 시술', pattern: /안전[한]?\s*(시술|수술|치료)[이가]?\s*보장|100%\s*안전/ },
  { label: '통증 없는', pattern: /통증\s*없[는이]|무통[증]?[의]?\s*(시술|수술|치료)|아프지\s*않[은는]/ },
  { label: '흉터 없는', pattern: /흉터\s*(없|zero|제로|걱정\s*없|남지\s*않)/ },
]

function readViolationsFromError(error: unknown): string[] {
  if (!(error instanceof ApiError)) return []
  const detail = error.detail
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const violations = (detail as { violations?: unknown }).violations
    if (Array.isArray(violations)) return violations.map((v) => String(v))
  }
  return []
}

function checkForbidden(text: string): string[] {
  if (!text) return []
  return FORBIDDEN_RULES.filter((rule) => rule.pattern.test(text)).map((rule) => rule.label)
}

function highlightForbidden(text: string, violations: string[]): string {
  if (violations.length === 0) return text
  let result = text
  for (const label of violations) {
    const rule = FORBIDDEN_RULES.find((r) => r.label === label)
    if (rule) {
      result = result.replace(rule.pattern, (match) => `【${match}】`)
    } else {
      // fallback: label 자체가 본문에 포함되어 있으면 하이라이트
      result = result.split(label).join(`【${label}】`)
    }
  }
  return result
}

function getContentTypeLabel(item: ContentItem): string {
  return item.display?.content_type_label ?? TYPE_LABELS[item.content_type] ?? '콘텐츠 유형 확인 필요'
}

function getEssenceLabel(item: ContentItem): { label: string; color: string } {
  if (!item.essence_status) return ESSENCE_FALLBACK
  const fallback = ESSENCE_LABELS[item.essence_status] ?? { label: '운영 기준 확인 필요', color: 'bg-slate-100 text-slate-700' }
  return { ...fallback, label: item.display?.essence_status_label ?? fallback.label }
}

/** 상세 헤더의 상태 한 줄. 예정일은 아직 그 날짜가 의미를 갖는 상태에서만 붙인다 —
 * 종료된 항목에 "예정"을 쓰면 앞으로 발행될 글처럼 읽힌다. */
function describeDetailStatus(item: ContentItem, label: string): string {
  if (item.published_at) return `${label} · ${formatDateTime(item.published_at)} 공개분`
  const kind = item.row_state.kind
  if (kind === 'scheduled' || kind === 'generating') return `${label} · ${item.scheduled_date} 예정`
  if (kind === 'blocked') return `${label} · 발행 예정일 ${item.scheduled_date}`
  return label
}

/** 연결 여부만 읽기 전용으로 보여 준다 — 내부 식별자는 화면에 흘리지 않는다. */
function describeGuideLink(text: unknown, linkedId: string | null | undefined): string {
  if (typeof text === 'string' && text.trim()) return text.trim()
  return linkedId ? '연결됨' : '미연결'
}

export default function ContentPage() {
  const { id } = useParams<{ id: string }>()
  // 레이아웃이 이미 병원 정보를 들고 있다 — 공개 링크(aeo_domain/공개 주소)도 여기서 읽는다.
  const { hospital, refetch: refetchHeader } = useHospitalHeader()
  const aeoDomain = hospital?.aeo_domain ?? null
  const publicHost = aeoDomain ?? platformSubdomainHost(hospital?.slug)

  // Month filter
  const [year, setYear] = useState(new Date().getFullYear())
  const [month, setMonth] = useState(new Date().getMonth() + 1)

  // List state
  const [items, setItems] = useState<ContentItem[]>([])
  const [activeFilter, setActiveFilter] = useState<ContentRowFilter>('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [currentOperatorName, setCurrentOperatorName] = useState<string | null>(null)

  // Detail / edit modal
  const [selected, setSelected] = useState<ContentItem | null>(null)
  const dialogCloseRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const referencesEditorRef = useRef<HTMLDivElement>(null)
  const destructiveConfirmRef = useRef<HTMLButtonElement>(null)
  const deepLinkOpenedRef = useRef(false)
  const [actionLoading, setActionLoading] = useState(false)
  // 사람이 되돌릴 수 없게 만드는 유일한 조작 — 표본에서 문제를 찾았을 때의 비공개.
  const [confirmAction, setConfirmAction] = useState<'reject' | null>(null)
  const confirmActionRef = useRef<'reject' | null>(null)

  // Inline edit
  const [editMode, setEditMode] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editBody, setEditBody] = useState('')
  const [editMeta, setEditMeta] = useState('')
  const [editReferences, setEditReferences] = useState<ContentReference[]>([])
  const [violations, setViolations] = useState<string[]>([])
  const [editError, setEditError] = useState<string | null>(null)
  const [editSaving, setEditSaving] = useState(false)
  // 세션 만료(401) 재로그인 후에도 편집 중이던 내용을 복구할 수 있게 남겨두는 스냅샷 —
  // 복구 가능한 초안이 있을 때만 편집 화면 상단에 배너로 안내한다.
  const [recoverableDraft, setRecoverableDraft] = useState<ContentDraftSnapshot | null>(null)

  // 페이지 단위 액션 피드백 — 모달이 닫혀 있어도 결과를 보여준다.
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionSuccess, setActionSuccess] = useState<string | null>(null)

  // 월을 빠르게 바꾸면 느린 이전 응답이 나중에 도착해 새 선택을 덮어쓴다. 요청마다
  // 세대 번호를 매겨 최신 요청의 응답만 화면에 반영한다.
  const loadGenerationRef = useRef(0)
  const detailGenerationRef = useRef(0)

  const load = useCallback(() => {
    const generation = (loadGenerationRef.current += 1)
    const fresh = () => generation === loadGenerationRef.current
    setLoading(true)
    setError(null)
    fetchAPI<ContentItem[]>(`/admin/hospitals/${id}/content?year=${year}&month=${month}`)
      .then((rows) => { if (fresh()) setItems(rows) })
      .catch((reason: unknown) => {
        if (!isExpectedOperatorRequestFailure(reason)) throw reason
        if (fresh()) setError(safeOperatorError('content', '콘텐츠 목록 다시 불러오기를 누르세요.'))
      })
      .finally(() => { if (fresh()) setLoading(false) })
  }, [id, month, year])

  const closeDetail = useCallback(() => {
    setSelected(null)
    setEditMode(false)
    setRecoverableDraft(null)
    setConfirmAction(null)
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    let cancelled = false
    void fetchCurrentAccount().then((account) => {
      if (!cancelled) setCurrentOperatorName(resolveAuditActorName(account?.name))
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    confirmActionRef.current = confirmAction
    if (confirmAction) window.setTimeout(() => destructiveConfirmRef.current?.focus(), 0)
  }, [confirmAction])

  // 편집 중 세션이 만료돼 401 리다이렉트가 발생해도 내용을 잃지 않도록 필드가
  // 바뀔 때마다 병원id+콘텐츠id 키로 스냅샷을 남긴다. 저장 성공 시에만 지운다.
  // 단, 편집 진입 직후(사용자가 아직 아무것도 바꾸지 않은 원본 상태)에는 저장하지
  // 않는다 — 그렇지 않으면 세션 만료 전 남겨둔 복구용 스냅샷을 미편집 원본으로 즉시
  // 덮어써 이중 401/새로고침 시 복구가 불가능해진다 (dirty 가드).
  useEffect(() => {
    if (!editMode || !selected) return
    const current = {
      title: editTitle,
      body: editBody,
      meta_description: editMeta,
      references: editReferences,
    }
    const original = {
      title: selected.title ?? '',
      body: selected.body ?? '',
      meta_description: selected.meta_description ?? '',
      references: (selected.references ?? []).map((ref) => ({ title: ref.title ?? '', url: ref.url ?? '' })),
    }
    if (!editFieldsDiffer(current, original)) return
    saveDraftSnapshot(id, selected.id, current)
  }, [editMode, selected, id, editTitle, editBody, editMeta, editReferences])

  useEffect(() => {
    if (!selected) return
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const mainContent = document.getElementById('main-content')
    mainContent?.setAttribute('inert', '')
    mainContent?.setAttribute('aria-hidden', 'true')
    document.body.style.overflow = 'hidden'
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (confirmActionRef.current) {
          setConfirmAction(null)
          return
        }
        closeDetail()
        return
      }
      // 의존성 없는 기본 Tab 포커스 트랩 — 포커스가 모달 밖으로 빠져나가지 않게 한다.
      if (event.key !== 'Tab') return
      const dialog = dialogRef.current
      if (!dialog) return
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((el) => el.offsetParent !== null)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey) {
        if (active === first || !dialog.contains(active)) {
          event.preventDefault()
          last.focus()
        }
      } else if (active === last || !dialog.contains(active)) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    window.setTimeout(() => dialogCloseRef.current?.focus(), 0)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      mainContent?.removeAttribute('inert')
      mainContent?.removeAttribute('aria-hidden')
      document.body.style.overflow = ''
      previousFocus?.focus()
    }
  }, [closeDetail, selected])

  // 운영 센터의 콘텐츠 딥링크(?content=)로 들어오면 그 글을 바로 연다.
  useEffect(() => {
    if (deepLinkOpenedRef.current) return
    const contentId = new URLSearchParams(window.location.search).get('content')
    if (!contentId) return
    deepLinkOpenedRef.current = true
    setEditMode(false)
    setEditError(null)
    setViolations([])
    void fetchAPI<ContentItem>(`/admin/hospitals/${id}/content/${contentId}`)
      .then(setSelected)
      .catch(() => {
        setActionError(safeOperatorError('content', '목록에서 해당 콘텐츠의 ‘상세’를 다시 누르세요.'))
      })
  }, [id])

  // 전월 이월 콘텐츠 항목은 이번 달 최우선 처리 대상 — 목록 맨 위로 끌어올린다 (나머지는 기존 순서 유지).
  const sortedItems = useMemo(() => sortCarriedOverFirst(items), [items])
  const filteredItems = useMemo(
    () => sortedItems.filter((item) => matchesRowFilter(item, activeFilter)),
    [activeFilter, sortedItems],
  )
  const summary = useMemo(() => summarizeRows(items), [items])

  function clearActionFeedback() {
    setActionError(null)
    setActionSuccess(null)
  }

  // 단건 액션 후 월 전체를 다시 불러오는 대신, 바뀐 아이템 하나만 다시 받아 목록/상세에
  // 병합한다. 실패해도 조용히 넘어간다 — 다음 자연스러운 새로고침에서 맞춰진다.
  const refreshItem = useCallback(async (
    itemId: string,
    { preserveOpenEditor = false }: { preserveOpenEditor?: boolean } = {},
  ) => {
    try {
      const full = await fetchAPI<ContentItem>(`/admin/hospitals/${id}/content/${itemId}`)
      // 반려는 scheduled_date가 오늘 이하면 내일로 재발행 일정한다 — 월말 반려는 다음
      // 달로 넘어간다. 그 결과를 이번 달 목록에 그대로 병합하면 다음 달 콘텐츠 항목이 이번 달
      // 화면에 유령처럼 남으므로, 조회 중인 연/월과 다르면 병합 대신 제거한다.
      if (!belongsToMonthView(full, year, month)) {
        setItems((prev) => prev.filter((it) => it.id !== full.id))
        setSelected((prev) => (prev && prev.id === full.id ? null : prev))
        return true
      }
      setItems((prev) => prev.map((it) => (it.id === full.id ? full : it)))
      setSelected((prev) => {
        if (!prev || prev.id !== full.id) return prev
        if (preserveOpenEditor && editMode) return prev
        return full
      })
      return true
    } catch {
      return false
    }
  }, [editMode, id, year, month])

  const retryRefreshItem = useCallback((itemId: string) => {
    let attempt = 0
    const retry = () => {
      void refreshItem(itemId, { preserveOpenEditor: true }).then((updated) => {
        if (updated || attempt >= 5) return
        attempt += 1
        window.setTimeout(retry, 5000)
      })
    }
    retry()
  }, [refreshItem])

  async function handlePostPublishReview(itemId: string) {
    setActionLoading(true)
    clearActionFeedback()
    try {
      await fetchAPI(`/admin/hospitals/${id}/content/${itemId}/post-publish-review`, {
        method: 'POST',
        body: JSON.stringify({}),
      })
      setActionSuccess('공개 내용 확인을 완료로 기록했습니다.')
    } catch (e: unknown) {
      // 서버는 보류 사유를 문자열 detail에 담아 409로 거절한다. 일반 안내로 덮으면 AE는
      // 같은 버튼을 다시 누를 뿐이므로 사유를 그대로 보여 주고, 최신 상태를 다시 읽어
      // 오래된 탭에서는 버튼 자체가 사라지게 한다.
      const serverDetail = e instanceof ApiError && typeof e.detail === 'string' ? e.message : null
      setEditError(serverDetail ?? safeOperatorError('content', '최신 공개 글을 확인한 뒤 ‘문제 없음’을 다시 누르세요.'))
      void refreshItem(itemId)
      setActionLoading(false)
      return
    }
    try {
      const full = await fetchAPI<ContentItem>(`/admin/hospitals/${id}/content/${itemId}`)
      setSelected(full)
      setItems((prev) => prev.map((it) => (it.id === full.id ? full : it)))
    } catch {
      setActionSuccess('공개 내용 확인을 완료로 기록했습니다. 최신 표시만 다시 불러오는 중입니다.')
      retryRefreshItem(itemId)
    } finally {
      setActionLoading(false)
    }
  }

  async function handleReject(itemId: string) {
    setActionLoading(true)
    setConfirmAction(null)
    clearActionFeedback()
    try {
      await fetchAPI(`/admin/hospitals/${id}/content/${itemId}/reject`, { method: 'POST' })
      setActionSuccess('콘텐츠를 비공개했습니다. 야간에 재생성됩니다.')
      void refetchHeader()
      // 닫기 전에 그 글을 다시 읽는다 — 반려로 다음 달로 밀린 글은 `refreshItem`이 이번
      // 달 목록에서 빼고, 실패하면 기존 재시도에 맡긴다. 목록이 옛 상태로 남으면 AE는
      // 방금 내린 글을 여전히 "공개 중"으로 본다.
      if (!(await refreshItem(itemId))) retryRefreshItem(itemId)
      setSelected(null)
    } catch (e: unknown) {
      const message = safeOperatorError('content', '최신 상태를 다시 확인한 뒤 ‘문제 발견’을 다시 누르세요.')
      if (selected && selected.id === itemId) setEditError(message)
      else setActionError(message)
    } finally {
      setActionLoading(false)
    }
  }

  async function openDetail(item: ContentItem) {
    const generation = (detailGenerationRef.current += 1)
    setEditMode(false)
    setEditError(null)
    setViolations([])
    try {
      const full = await fetchAPI<ContentItem>(`/admin/hospitals/${id}/content/${item.id}`)
      if (generation === detailGenerationRef.current) setSelected(full)
    } catch {
      if (generation === detailGenerationRef.current) setSelected(item)
    }
  }

  function enterEditMode() {
    if (!selected) return
    setEditTitle(selected.title ?? '')
    setEditBody(selected.body ?? '')
    setEditMeta(selected.meta_description ?? '')
    setEditReferences((selected.references ?? []).map((ref) => ({ title: ref.title ?? '', url: ref.url ?? '' })))
    setViolations([])
    setEditError(null)
    setEditMode(true)

    // 세션 만료로 저장하지 못한 채 남아 있는 스냅샷이 있으면 복구 배너로 안내한다.
    const draft = readDraftSnapshot(id, selected.id)
    if (draft && draftDiffersFromCurrent(draft, {
      title: selected.title ?? '',
      body: selected.body ?? '',
      meta_description: selected.meta_description ?? '',
      references: (selected.references ?? []).map((ref) => ({ title: ref.title ?? '', url: ref.url ?? '' })),
    })) {
      setRecoverableDraft(draft)
    } else {
      if (draft) clearDraftSnapshot(id, selected.id)
      setRecoverableDraft(null)
    }
  }

  function enterReferenceEditMode() {
    enterEditMode()
    if ((selected?.references?.length ?? 0) === 0) setEditReferences([{ title: '', url: '' }])
    window.setTimeout(() => {
      referencesEditorRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      referencesEditorRef.current?.querySelector<HTMLInputElement>('input')?.focus()
    }, 0)
  }

  function restoreDraft() {
    if (!recoverableDraft) return
    setEditTitle(recoverableDraft.title)
    setEditBody(recoverableDraft.body)
    setEditMeta(recoverableDraft.meta_description)
    setEditReferences(recoverableDraft.references)
    setViolations(checkForbidden(`${recoverableDraft.title} ${recoverableDraft.body} ${recoverableDraft.meta_description}`))
    setRecoverableDraft(null)
  }

  function discardDraft() {
    if (!selected) return
    clearDraftSnapshot(id, selected.id)
    setRecoverableDraft(null)
  }

  function updateReference(index: number, key: 'title' | 'url', value: string) {
    setEditReferences((prev) => prev.map((ref, i) => (i === index ? { ...ref, [key]: value } : ref)))
  }

  function addReferenceRow() {
    setEditReferences((prev) => [...prev, { title: '', url: '' }])
  }

  function removeReferenceRow(index: number) {
    setEditReferences((prev) => prev.filter((_, i) => i !== index))
  }

  function handleEditBodyChange(val: string) {
    setEditBody(val)
    const found = checkForbidden(`${editTitle} ${val} ${editMeta}`)
    setViolations(found)
  }

  function handleEditTitleChange(val: string) {
    setEditTitle(val)
    const found = checkForbidden(`${val} ${editBody} ${editMeta}`)
    setViolations(found)
  }

  function handleEditMetaChange(val: string) {
    setEditMeta(val)
    const found = checkForbidden(`${editTitle} ${editBody} ${val}`)
    setViolations(found)
  }

  async function handleSaveEdit() {
    if (!selected) return
    const allText = `${editTitle} ${editBody} ${editMeta}`
    const found = checkForbidden(allText)
    if (found.length > 0) {
      setViolations(found)
      setEditError(`금지 표현이 포함되어 있습니다: ${found.join(', ')}`)
      return
    }
    const cleanedReferences = editReferences
      .map((ref) => ({ title: ref.title.trim(), url: ref.url.trim() }))
      .filter((ref) => ref.title || ref.url)
    if (cleanedReferences.some((ref) => !ref.title || !ref.url)) {
      setEditError('참고 자료는 제목과 URL을 모두 입력해 주세요.')
      return
    }
    setEditSaving(true)
    setEditError(null)
    try {
      const updated = await fetchAPI<ContentItem>(`/admin/hospitals/${id}/content/${selected.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          title: editTitle,
          body: editBody,
          meta_description: editMeta,
          references: cleanedReferences,
        }),
      })
      clearDraftSnapshot(id, selected.id)
      setRecoverableDraft(null)
      setSelected(updated)
      setEditMode(false)
      setItems((prev) => prev.map((it) => (it.id === updated.id ? updated : it)))
    } catch (e: unknown) {
      const violationList = readViolationsFromError(e)
      if (violationList.length > 0) {
        setViolations(violationList)
        setEditError(`금지 표현: ${violationList.join(', ')}`)
      } else {
        setEditError(safeOperatorError('content', '입력 내용을 확인한 뒤 ‘저장’을 다시 누르세요.'))
      }
    } finally {
      setEditSaving(false)
    }
  }

  const currentYear = new Date().getFullYear()
  const yearOptions = [currentYear - 1, currentYear, currentYear + 1]
  const monthOptions = Array.from({ length: 12 }, (_, i) => i + 1)

  const selectedRow = selected ? describeRowState(selected.row_state) : null
  const selectedNotification = selected ? getPublishNotificationPresentation(selected) : null
  const selectedPublicUrl = selected ? buildPublicContentUrl(publicHost, selected.id) : null
  const selectedVisible = selected?.compliance?.public_visibility?.visible === true
  const selectedSample = selected ? canConfirmSample(selected) : false
  // 금지 표현 검출도 backend compliance가 단일 기준 (FAQ 분리 필드까지 포함해 검사한다).
  // FORBIDDEN_RULES는 편집 모드의 실시간 힌트 용도로만 유지.
  const selectedTextViolations = selected?.compliance.forbidden_violations ?? []
  const selectedFindings: string[] = Array.isArray(selected?.essence_check_summary?.findings)
    ? (selected!.essence_check_summary!.findings as unknown[]).map((f) => String(f))
    : []
  const applyRowFilter = (filter: ContentRowFilter) => {
    setActiveFilter(filter)
    window.requestAnimationFrame(() => {
      document.getElementById('content-operations-list')?.scrollIntoView({
        behavior: 'smooth',
        block: 'start',
      })
    })
  }

  return (
    <div className="p-4 sm:p-6 lg:p-8">
      {/* Hero / summary header */}
      <div className="mb-6">
        <div data-current-task className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="text-2xl font-bold text-slate-900">콘텐츠</h2>
            <p className="text-sm text-slate-500 mt-1">
              발행일 08:00 자동 공개, 사람은 표본 확인만 합니다.
            </p>
          </div>
        </div>

        {actionError && (
          <div className="mt-4"><OperatorIssuePanel message={actionError} surface="content" /></div>
        )}
        {actionSuccess && (
          <DismissibleBanner tone="success" message={actionSuccess} dismissLabel="완료 메시지 닫기" onClose={() => setActionSuccess(null)} />
        )}

        {/* 발행 요일은 이 화면에서 정한다 — 저장하면 이번 달 표와 헤더 상태를 다시 읽는다. */}
        <ScheduleSection
          hospitalId={id}
          onSaved={() => {
            load()
            void refetchHeader()
          }}
        />

        {/* 카드는 공개 사이트와 같은 판정을 그대로 센다 — 화면이 따로 묶지 않는다. */}
        <div className="mt-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <SummaryCard label="공개 중" value={summary.public} tone="green" hint="공개 페이지에 게시됨" filter="public" activeFilter={activeFilter} onFilter={applyRowFilter} />
          <SummaryCard label="공개 보류" value={summary.withheld} tone="amber" hint="발행됐지만 공개 페이지에 없음" filter="withheld" activeFilter={activeFilter} onFilter={applyRowFilter} />
          <SummaryCard label="예정" value={summary.scheduled} tone="blue" hint="발행일 08:00 자동 공개" filter="scheduled" activeFilter={activeFilter} onFilter={applyRowFilter} />
          <SummaryCard label="초안 생성 중" value={summary.generating} tone="gray" hint="자동 생성 대기" filter="generating" activeFilter={activeFilter} onFilter={applyRowFilter} />
          <SummaryCard label="차단" value={summary.blocked} tone="orange" hint={blockedHint(items)} filter="blocked" activeFilter={activeFilter} onFilter={applyRowFilter} />
          {/* 종료도 행 상태다 — 카드가 없으면 같은 이름의 필터로 갈 방법이 없다. */}
          <SummaryCard label="종료" value={summary.closed} tone="gray" hint="자동 생성·발행에서 제외" filter="closed" activeFilter={activeFilter} onFilter={applyRowFilter} />
          {summary.carried > 0 && (
            <SummaryCard label="이월" value={summary.carried} tone="amber" hint="전월에서 이월됨" filter="carried" activeFilter={activeFilter} onFilter={applyRowFilter} />
          )}
        </div>
        {activeFilter !== 'all' && (
          <div className="mt-3 flex items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-800">
            <span>선택한 상태만 표시 중 · {filteredItems.length}건</span>
            <button type="button" onClick={() => setActiveFilter('all')} className="min-h-11 rounded-md px-2.5 font-semibold hover:bg-blue-100">
              전체 보기
            </button>
          </div>
        )}
      </div>

      {/* Month filter */}
      <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="content-year" className="sr-only">조회 연도</label>
        <select
          id="content-year"
          value={year}
          onChange={(e) => setYear(Number(e.target.value))}
          className="min-h-11 px-3 py-1.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {yearOptions.map((y) => (
            <option key={y} value={y}>{y}년</option>
          ))}
        </select>
        <label htmlFor="content-month" className="sr-only">조회 월</label>
        <select
          id="content-month"
          value={month}
          onChange={(e) => setMonth(Number(e.target.value))}
          className="min-h-11 px-3 py-1.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {monthOptions.map((m) => (
            <option key={m} value={m}>{m}월</option>
          ))}
        </select>
        <span className="text-xs text-slate-500 ml-1">
          운영 기준·참고 자료·의료광고 금지 표현 검사는 자동 발행 직전에 다시 수행됩니다.
        </span>
        </div>
      </div>

      {loading && <div className="text-center py-16 text-slate-500">불러오는 중...</div>}

      {error && (
        <OperatorIssuePanel message={error} surface="content" onRetry={load} retryLabel="콘텐츠 목록 다시 불러오기" />
      )}

      {!loading && !error && (
        <div id="content-operations-list" className="scroll-mt-4 rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3 sm:px-6">
            <p className="text-sm font-semibold text-slate-800">콘텐츠 {filteredItems.length}건</p>
            <p className="text-xs text-slate-500">카드를 눌러 상태별로 좁혀볼 수 있습니다.</p>
          </div>
          <div className="divide-y divide-slate-100 lg:hidden">
            {filteredItems.length === 0 && (
              <div className="px-5 py-12 text-center text-sm text-slate-400">
                {items.length === 0 ? '이번 달 콘텐츠가 아직 없습니다.' : '선택한 상태의 콘텐츠가 없습니다.'}
              </div>
            )}
            {filteredItems.map((item) => {
              const row = describeRowState(item.row_state)
              return (
                <article key={item.id} className="p-4 sm:p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        <span>{item.scheduled_date}</span>
                        <span aria-hidden>·</span>
                        <span>{getContentTypeLabel(item)}</span>
                        <span aria-hidden>·</span>
                        <span>{item.sequence_no}/{item.total_count}</span>
                      </div>
                      <h3 className="mt-2 text-sm font-semibold text-slate-900">
                        {item.title ?? <span className="font-normal italic text-slate-400">제목 준비 중</span>}
                      </h3>
                    </div>
                    <span className={`inline-flex shrink-0 rounded-full px-2.5 py-1 text-xs font-medium ${ROW_TONE_BADGES[row.tone]}`}>
                      {row.label}
                    </span>
                  </div>
                  {row.detail && (
                    <p className="mt-2 break-keep [overflow-wrap:anywhere] text-xs text-slate-600">{row.detail}</p>
                  )}
                  {row.href && (
                    <a href={row.href} className="mt-1 inline-flex min-h-11 items-center text-xs font-semibold text-blue-700 underline underline-offset-2">
                      운영 센터에서 조치
                    </a>
                  )}
                  {item.carried_over_from && (
                    <span className="mt-2 inline-flex rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700">
                      전월 이월 · 우선 처리
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => openDetail(item)}
                    className="mt-3 inline-flex min-h-11 w-full items-center justify-center rounded-lg border border-slate-300 px-3 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    {canConfirmSample(item) ? '확인' : '상세'}
                  </button>
                </article>
              )
            })}
          </div>
          <div className="hidden overflow-x-auto lg:block">
          <table className="min-w-[1080px] w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="whitespace-nowrap break-keep text-left px-6 py-3 text-slate-600 font-medium">발행일</th>
                <th className="whitespace-nowrap break-keep text-left px-6 py-3 text-slate-600 font-medium">유형</th>
                <th className="text-left px-6 py-3 text-slate-600 font-medium">제목</th>
                <th className="whitespace-nowrap break-keep text-center px-6 py-3 text-slate-600 font-medium">순번</th>
                <th className="whitespace-nowrap break-keep text-center px-6 py-3 text-slate-600 font-medium">상태</th>
                <th className="whitespace-nowrap break-keep text-right px-6 py-3 text-slate-600 font-medium">액션</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {filteredItems.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-center py-12 text-slate-400 text-sm">
                    {items.length === 0 ? '이번 달 콘텐츠가 아직 없습니다.' : '선택한 상태의 콘텐츠가 없습니다.'}
                    <br />
                    {items.length === 0 && <span className="text-slate-500">발행 일정 탭에서 월 발행 편수를 확인하거나 야간 생성 결과를 기다려 주세요.</span>}
                  </td>
                </tr>
              )}
              {filteredItems.map((item) => {
                const row = describeRowState(item.row_state)
                return (
                  <tr key={item.id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-6 py-4 text-slate-600">
                      <div>{item.scheduled_date}</div>
                      {item.published_at && <span className="mt-1 block text-[11px] text-slate-400">{formatDateTime(item.published_at)} 공개분</span>}
                      {item.carried_over_from && (
                        <span
                          title={`원래 예정일: ${formatDate(item.carried_over_from)}`}
                          className="mt-1 inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-50 text-amber-700 border border-amber-200"
                        >
                          전월 이월 — 우선 처리
                        </span>
                      )}
                    </td>
                    <td className="whitespace-nowrap break-keep px-6 py-4 text-slate-600">{getContentTypeLabel(item)}</td>
                    <td className="px-6 py-4">
                      <button onClick={() => openDetail(item)} className="inline-flex min-h-11 items-center text-left text-blue-600 hover:underline">
                        {item.title ?? <span className="text-slate-400 italic">제목 준비 중</span>}
                      </button>
                    </td>
                    <td className="px-6 py-4 text-center text-slate-500">
                      {item.sequence_no}/{item.total_count}
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className={`inline-flex whitespace-nowrap break-keep px-2.5 py-0.5 rounded-full text-xs font-medium ${ROW_TONE_BADGES[row.tone]}`}>
                        {row.label}
                      </span>
                      {row.detail && (
                        <span className="mt-1 block break-keep [overflow-wrap:anywhere] text-[11px] text-slate-600">{row.detail}</span>
                      )}
                      {row.href && (
                        <a href={row.href} className="mt-1 inline-flex min-h-11 items-center text-[11px] font-semibold text-blue-700 underline underline-offset-2">
                          운영 센터에서 조치
                        </a>
                      )}
                    </td>
                    <td className="px-6 py-4 text-right">
                      <button
                        onClick={() => openDetail(item)}
                        className={`inline-flex min-h-11 items-center rounded-md px-2.5 text-xs font-medium hover:bg-slate-100 ${canConfirmSample(item) ? 'text-blue-700' : 'text-slate-600'}`}
                      >
                        {canConfirmSample(item) ? '확인' : '상세'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          </div>
        </div>
      )}

      <ReadOnlySignals hospitalId={id} year={year} month={month} />

      {/* Detail / Edit Modal */}
      {selected && selectedRow && createPortal((
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target !== event.currentTarget) return
            if (confirmAction) setConfirmAction(null)
            else closeDetail()
          }}
        >
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="content-dialog-title"
            aria-describedby="content-dialog-status"
            className={`flex max-h-[90vh] w-full flex-col overflow-hidden rounded-xl bg-white shadow-xl ${editMode ? 'max-w-5xl' : 'max-w-2xl'}`}
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="flex shrink-0 items-start justify-between gap-3 border-b border-slate-200 p-4 sm:p-6">
              <div>
                <span className="text-xs font-medium text-slate-500 uppercase">
                  {getContentTypeLabel(selected)}
                </span>
                {selected.carried_over_from && (
                  <span
                    title={`원래 예정일: ${formatDate(selected.carried_over_from)}`}
                    className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-amber-50 text-amber-700 border border-amber-200"
                  >
                    전월 이월 — 우선 처리
                  </span>
                )}
                {!editMode && (
                  <h3 id="content-dialog-title" className="text-lg font-bold text-slate-900 mt-0.5">{selected.title ?? '제목 준비 중'}</h3>
                )}
                {editMode && <h3 id="content-dialog-title" className="text-lg font-bold text-slate-900 mt-0.5">콘텐츠 편집</h3>}
                <p id="content-dialog-status" className="mt-1 text-xs text-slate-500">
                  {describeDetailStatus(selected, selectedRow.label)}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {!editMode && (selected.status === 'DRAFT' || selected.status === 'PUBLISHED') && (
                  <button
                    onClick={enterEditMode}
                    className="hidden min-h-11 px-3 py-1.5 text-sm font-medium text-blue-600 border border-blue-300 rounded-lg hover:bg-blue-50 sm:inline-flex sm:items-center"
                  >
                    편집
                  </button>
                )}
                <button
                  ref={dialogCloseRef}
                  onClick={closeDetail}
                  aria-label="콘텐츠 상세 닫기"
                  className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md text-xl text-slate-400 hover:bg-slate-100 hover:text-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  ✕
                </button>
              </div>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            {editError && (
              <div className="mx-6 mt-4">
                <OperatorIssuePanel message={editError} surface="content" />
                {violations.length > 0 && (
                  <ul className="mt-2 list-inside list-disc text-xs text-red-800">
                    {violations.map((v) => <li key={v}>{v}</li>)}
                  </ul>
                )}
              </div>
            )}

            {editMode ? (
              /* Edit split view */
              <div className="p-6">
                {recoverableDraft && (
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
                    <span>
                      세션이 만료되기 전 편집하던 내용이 남아 있습니다 ({formatDateTime(new Date(recoverableDraft.savedAt).toISOString())} 저장).
                      복구하시겠습니까?
                    </span>
                    <div className="flex shrink-0 gap-2">
                      <button
                        type="button"
                        onClick={restoreDraft}
                        className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
                      >
                        복구
                      </button>
                      <button
                        type="button"
                        onClick={discardDraft}
                        className="rounded-md border border-blue-300 bg-white px-3 py-1.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
                      >
                        무시
                      </button>
                    </div>
                  </div>
                )}
                <div className="mb-4">
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">제목</label>
                  <input
                    type="text"
                    value={editTitle}
                    onChange={(e) => handleEditTitleChange(e.target.value)}
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">검색 미리보기 설명</label>
                  <input
                    type="text"
                    value={editMeta}
                    onChange={(e) => handleEditMetaChange(e.target.value)}
                    maxLength={300}
                    className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <p className="text-[11px] text-slate-400 mt-1 text-right">{editMeta.length}/300</p>
                </div>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-slate-700 mb-1.5">본문 (마크다운)</label>
                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                    <div>
                      <p className="text-xs text-slate-400 mb-1">편집</p>
                      <textarea
                        value={editBody}
                        onChange={(e) => handleEditBodyChange(e.target.value)}
                        rows={18}
                        className={`w-full px-3 py-2 border rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none ${violations.length > 0 ? 'border-red-400 bg-red-50' : 'border-slate-300'}`}
                      />
                      {violations.length > 0 && (
                        <p className="text-xs text-red-600 mt-1">
                          금지 표현: {violations.join(', ')}
                        </p>
                      )}
                    </div>
                    <div>
                      <p className="text-xs text-slate-400 mb-1">미리보기</p>
                      <div className="h-full border border-slate-200 rounded-lg p-3 overflow-auto bg-slate-50">
                        <div className="prose prose-sm max-w-none text-slate-700">
                          <ReactMarkdown>
                            {violations.length > 0 ? highlightForbidden(editBody, violations) : editBody}
                          </ReactMarkdown>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                {/* 참고 자료 편집 — 자동 발행 안전검사: 권위 있는 참고 자료 1개 이상 필요 */}
                <div ref={referencesEditorRef} className="mb-4 scroll-mt-4">
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="block text-sm font-medium text-slate-700">참고 자료</label>
                    <button
                      type="button"
                      onClick={addReferenceRow}
                      className="px-2.5 py-1 text-xs font-medium text-blue-600 border border-blue-300 rounded-lg hover:bg-blue-50"
                    >
                      + 참고 자료 추가
                    </button>
                  </div>
                  <p className="text-xs text-slate-500 mb-2">
                    자동 발행하려면 권위 있는 참고 자료(학회·공공기관 등)가 1개 이상 필요합니다.
                  </p>
                  {editReferences.length === 0 && (
                    <p className="rounded-lg border border-dashed border-slate-300 bg-slate-50 px-3 py-2.5 text-xs text-slate-500">
                      등록된 참고 자료가 없습니다. 위의 버튼으로 추가해 주세요.
                    </p>
                  )}
                  <div className="space-y-2">
                    {editReferences.map((ref, idx) => (
                      <div key={idx} className="flex items-start gap-2">
                        <input
                          type="text"
                          value={ref.title}
                          onChange={(e) => updateReference(idx, 'title', e.target.value)}
                          placeholder="자료 제목 (예: 대한대장항문학회 진료지침)"
                          aria-label={`참고 자료 ${idx + 1} 제목`}
                          className="w-2/5 px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                        <input
                          type="url"
                          value={ref.url}
                          onChange={(e) => updateReference(idx, 'url', e.target.value)}
                          placeholder="https://..."
                          aria-label={`참고 자료 ${idx + 1} URL`}
                          className="flex-1 px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                        />
                        <button
                          type="button"
                          onClick={() => removeReferenceRow(idx)}
                          aria-label={`참고 자료 ${idx + 1} 제거`}
                          className="mt-2 text-slate-400 hover:text-red-500 text-lg leading-none"
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
                <p className="mb-3 text-xs text-slate-500">{EDIT_SAVE_NOTICE}</p>
                <div className="flex gap-3">
                  <button
                    onClick={handleSaveEdit}
                    disabled={editSaving || violations.length > 0}
                    className="px-5 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50"
                  >
                    {editSaving ? '저장 중...' : '저장'}
                  </button>
                  <button
                    onClick={() => {
                      // 취소 = 편집 폐기. 남아 있는 스냅샷을 지우지 않으면 다음 편집
                      // 진입 시 허위 '세션 만료 복구' 배너가 뜬다.
                      if (selected) clearDraftSnapshot(id, selected.id)
                      setEditMode(false)
                      setViolations([])
                      setEditError(null)
                      setRecoverableDraft(null)
                    }}
                    className="px-5 py-2.5 bg-slate-100 text-slate-700 text-sm font-medium rounded-lg hover:bg-slate-200"
                  >
                    취소
                  </button>
                </div>
              </div>
            ) : (
              /* Read mode */
              <div className="p-6">
                <div className="mb-5 border border-slate-200 rounded-lg overflow-hidden">
                  <div className="px-4 py-2 bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600 uppercase tracking-wide">
                    답변 노출 콘텐츠 가이드
                  </div>
                  <div className="p-4 space-y-3 text-sm">
                    <BriefField
                      label="연결된 환자 질문"
                      value={describeGuideLink(selected.content_brief?.target_query, selected.query_target_id)}
                    />
                    <BriefField
                      label="연결된 노출 보완 작업"
                      value={describeGuideLink(null, selected.exposure_action_id)}
                    />
                  </div>
                </div>

                {/* 자동 안전검사 panel */}
                <div className="mb-5 border border-slate-200 rounded-lg overflow-hidden">
                  <div className="px-4 py-2 bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-600 uppercase tracking-wide">
                    자동 안전검사
                  </div>
                  <div className="p-4 space-y-3 text-sm">
                    <CheckRow
                      label="콘텐츠 운영 기준"
                      value={
                        selected.essence_status
                          ? getEssenceLabel(selected).label
                          : '미검수'
                      }
                      tone={selected.essence_status === 'ALIGNED' ? 'ok' : 'warn'}
                    />
                    {selectedFindings.length > 0 && (
                      <ul className="list-disc list-inside text-xs text-slate-600 pl-2">
                        {selectedFindings.map((finding, idx) => (
                          <li key={`${finding}-${idx}`}>{finding}</li>
                        ))}
                      </ul>
                    )}
                    <CheckRow
                      label="의료광고 금지 표현"
                      value={
                        selectedTextViolations.length === 0
                          ? '검출되지 않음'
                          : `검출: ${selectedTextViolations.join(', ')}`
                      }
                      tone={selectedTextViolations.length === 0 ? 'ok' : 'bad'}
                    />
                    <CheckRow
                      label="참고 자료"
                      value={
                        (selected.references?.length ?? 0) > 0
                          ? `${selected.references!.length}건 등록됨`
                          : '없음 — 자동 발행하려면 1개 이상 필요'
                      }
                      tone={(selected.references?.length ?? 0) > 0 ? 'ok' : 'warn'}
                    />
                    {(selected.references?.length ?? 0) > 0 && (
                      <ul className="list-disc list-inside text-xs text-slate-600 pl-2 space-y-0.5">
                        {selected.references!.map((ref, idx) => (
                          <li key={`${ref.url}-${idx}`}>
                            <a
                              href={ref.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-blue-600 hover:underline"
                            >
                              {ref.title || ref.url}
                            </a>
                          </li>
                        ))}
                      </ul>
                    )}
                    {/* 공개 여부는 공개 사이트와 같은 판정을 그대로 쓴다(H-01). */}
                    <CheckRow
                      label="공개 상태"
                      value={selectedRow.detail ? `${selectedRow.label} — ${selectedRow.detail}` : selectedRow.label}
                      tone={selectedRow.tone === 'good' ? 'ok' : selectedRow.tone === 'warn' ? 'bad' : 'warn'}
                    />
                  </div>
                </div>

                {selected.image_url && (
                  <div className="relative w-full h-48 rounded-lg overflow-hidden mb-4">
                    <Image src={selected.image_url} alt="" fill className="object-cover" />
                  </div>
                )}
                {selected.body && (
                  <div className="prose prose-sm max-w-none text-slate-700">
                    <ReactMarkdown>{selected.body}</ReactMarkdown>
                  </div>
                )}
              </div>
            )}

            </div>

            {!editMode && (
              <div className="shrink-0 border-t border-slate-200 bg-white p-4 shadow-[0_-8px_24px_rgba(15,23,42,0.08)] sm:p-5">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <div className="text-xs text-slate-600">
                    <span className={`mr-2 inline-flex rounded-full px-2.5 py-1 font-semibold ${ROW_TONE_BADGES[selectedRow.tone]}`}>{selectedRow.label}</span>
                    {selected.status === 'PUBLISHED' && selectedNotification?.state === 'SENT' && selected.post_publish_notified_at && `Slack 전달 ${formatDateTime(selected.post_publish_notified_at)}`}
                  </div>
                  {/* 공개 페이지가 숨기는 글에 링크를 걸면 AE는 404를 보고 원인을 모른다.
                      판정이 없으면 링크를 걸지 않는다 — 행 상태와 같은 fail-closed 기준이다. */}
                  {selected.status === 'PUBLISHED' &&
                    (selectedVisible ? (
                      selectedPublicUrl && (
                        <a
                          href={selectedPublicUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex min-h-11 items-center rounded-lg border border-blue-200 bg-blue-50 px-3 text-sm font-semibold text-blue-700 hover:bg-blue-100"
                        >
                          공개 사이트에서 보기 ↗
                        </a>
                      )
                    ) : (
                      <span className="text-sm text-amber-800">
                        공개 페이지에서 보류 중 — {selectedRow.detail ?? selectedRow.label}
                      </span>
                    ))}
                </div>
                {selected.status === 'PUBLISHED' && !['SENT', 'NOT_REQUIRED'].includes(selectedNotification?.state ?? '') && (
                  <div className="mb-3 break-keep [overflow-wrap:anywhere] rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                    <p className="font-semibold">Slack 알림 확인 필요</p>
                    {selectedNotification?.problem && <p className="mt-1"><strong>무슨 문제인가요?</strong> {selectedNotification.problem}</p>}
                    <p className="mt-1"><strong>발행 영향</strong> {selectedNotification?.publication_impact}</p>
                    <p className="mt-1"><strong>지금 할 일</strong> {selectedNotification?.next_action}</p>
                    <a href={hospitalOperationsHref(id, '/operations?queue=incidents')} className="mt-2 inline-flex min-h-11 items-center font-semibold text-blue-700 underline underline-offset-2">
                      운영 센터에서 알림 상태 확인
                    </a>
                  </div>
                )}
                {selectedRow.href && (
                  <div className="mb-3 rounded-lg border border-orange-200 bg-orange-50 px-3 py-2 text-xs text-orange-800">
                    <a href={selectedRow.href} className="inline-flex min-h-11 items-center font-semibold underline underline-offset-2">
                      운영 센터에서 조치: {selectedRow.detail ?? selectedRow.label}
                    </a>
                  </div>
                )}
                {selected.row_state.kind === 'blocked' && !['PUBLISHED', 'CANCELLED'].includes(selected.status) && (
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-orange-200 bg-orange-50 px-3 py-2">
                    <p className="text-xs text-orange-800">공개되지 않았습니다. 수정 저장 후 자동 안전검사를 다시 수행합니다.</p>
                    <button
                      type="button"
                      onClick={(selected.references?.length ?? 0) === 0 ? enterReferenceEditMode : enterEditMode}
                      className="inline-flex min-h-11 items-center rounded-md bg-white px-3 text-xs font-semibold text-orange-800 shadow-sm ring-1 ring-orange-200 hover:bg-orange-100"
                    >
                      콘텐츠 수정
                    </button>
                  </div>
                )}
                {selected.row_state.kind === 'scheduled' && (
                  <p className="mb-3 text-xs text-slate-500">
                    사전 승인 작업은 필요하지 않습니다. 발행 예정일 08:00에 자동 검증·공개되며, 예외가 남을 때만 Slack으로 알려드립니다.
                  </p>
                )}
                <div className="mb-3 flex gap-2 sm:hidden">
                  {(selected.status === 'DRAFT' || selected.status === 'PUBLISHED') && (
                    <button type="button" onClick={enterEditMode} className="min-h-11 flex-1 rounded-lg border border-blue-300 px-3 text-sm font-medium text-blue-700">콘텐츠 편집</button>
                  )}
                </div>

                {confirmAction ? (
                  <div role="alertdialog" aria-labelledby="destructive-action-title" className="rounded-lg border border-red-200 bg-red-50 p-3">
                    <p id="destructive-action-title" className="text-sm font-bold text-red-800">
                      이 콘텐츠를 즉시 비공개할까요?
                    </p>
                    <p className="mt-1 text-xs leading-relaxed text-red-700">
                      공개 사이트에서 바로 제거되고, 새 콘텐츠는 야간 재생성 주기에 만들어집니다.
                    </p>
                    <div className="mt-3 flex justify-end gap-2">
                      <button type="button" onClick={() => setConfirmAction(null)} className="min-h-11 rounded-lg border border-slate-300 bg-white px-4 text-sm font-medium text-slate-700 hover:bg-slate-50">취소</button>
                      <button
                        type="button"
                        ref={destructiveConfirmRef}
                        onClick={() => handleReject(selected.id)}
                        disabled={actionLoading}
                        className="min-h-11 rounded-lg bg-red-700 px-4 text-sm font-semibold text-white hover:bg-red-800 disabled:opacity-50"
                      >
                        {actionLoading ? '처리 중...' : '비공개 후 재생성'}
                      </button>
                    </div>
                  </div>
                ) : selected.status === 'PUBLISHED' ? (
                  <div className="flex flex-wrap gap-3">
                    {selected.post_publish_reviewed_at ? (
                      <div className="flex-1 min-w-48 rounded-lg border border-green-200 bg-green-50 px-4 py-2.5 text-center text-sm font-medium text-green-700">
                        {selected.post_publish_reviewed_by ?? currentOperatorName ?? '운영자'} · {formatDateTime(selected.post_publish_reviewed_at)} 확인 완료
                      </div>
                    ) : selectedSample ? (
                      <button
                        onClick={() => handlePostPublishReview(selected.id)}
                        disabled={actionLoading}
                        className="min-h-11 flex-1 min-w-48 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
                      >
                        문제 없음 · 확인 완료
                      </button>
                    ) : (
                      <div className="flex-1 min-w-48 break-keep [overflow-wrap:anywhere] rounded-lg border border-slate-200 bg-slate-50 px-4 py-2.5 text-sm text-slate-600">
                        {selectedVisible
                          ? '표본 확인 대상이 아닙니다. 사람이 할 일은 없습니다.'
                          : `공개 페이지에서 보류 중 — ${selectedRow.detail ?? selectedRow.label}`}
                      </div>
                    )}
                    {/* 결함이 확인된 공개 글은 표본 여부와 무관하게 사람이 내릴 수 있다
                        (의료광고 안전 통제). "문제 없음" 확인만 표본으로 제한한다. */}
                    <button
                      onClick={() => setConfirmAction('reject')}
                      disabled={actionLoading}
                      className="min-h-11 flex-1 min-w-48 rounded-lg bg-red-100 px-4 text-sm font-medium text-red-700 hover:bg-red-200 disabled:opacity-50"
                    >
                      문제 발견 · 비공개 후 재생성
                    </button>
                  </div>
                ) : selected.status === 'CANCELLED' ? (
                  <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-600">
                    이 콘텐츠 항목은 중복되었거나 발행 시점이 지나 종료됐으며, 자동 생성과 발행에서 제외됩니다.
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </div>
      ), document.body)}
    </div>
  )
}

function DismissibleBanner({
  tone,
  message,
  dismissLabel,
  onClose,
}: {
  tone: 'error' | 'success'
  message: string
  dismissLabel?: string
  onClose: () => void
}) {
  const styles =
    tone === 'error'
      ? { box: 'border-red-200 bg-red-50 text-red-700', close: 'text-red-400 hover:text-red-600' }
      : { box: 'border-green-200 bg-green-50 text-green-700', close: 'text-green-500 hover:text-green-700' }
  return (
    <div
      role="alert"
      className={`mt-4 flex items-start justify-between gap-3 rounded-lg border px-4 py-2.5 text-sm ${styles.box}`}
    >
      <span>{message}</span>
      <button
        type="button"
        onClick={onClose}
        aria-label={dismissLabel ?? '메시지 닫기'}
        className={`shrink-0 font-bold ${styles.close}`}
      >
        ✕
      </button>
    </div>
  )
}

function SummaryCard({
  label,
  value,
  tone,
  hint,
  filter,
  activeFilter,
  onFilter,
}: {
  label: string
  value: number
  tone: 'green' | 'orange' | 'gray' | 'blue' | 'amber'
  hint: string
  filter: ContentRowFilter
  activeFilter: ContentRowFilter
  onFilter: (filter: ContentRowFilter) => void
}) {
  const tones: Record<string, string> = {
    green: 'border-green-200 bg-green-50',
    orange: 'border-orange-200 bg-orange-50',
    gray: 'border-slate-200 bg-slate-50',
    blue: 'border-blue-200 bg-blue-50',
    amber: 'border-amber-200 bg-amber-50',
  }
  const numTones: Record<string, string> = {
    green: 'text-green-700',
    orange: 'text-orange-700',
    gray: 'text-slate-700',
    blue: 'text-blue-700',
    amber: 'text-amber-700',
  }
  const active = activeFilter === filter
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={() => onFilter(active ? 'all' : filter)}
      className={`min-h-24 rounded-xl border px-4 py-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${tones[tone]} ${active ? 'ring-2 ring-blue-600 ring-offset-2' : ''}`}
    >
      <p className="text-xs font-medium text-slate-600">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${numTones[tone]}`}>{value}</p>
      <p className="text-[11px] text-slate-500 mt-0.5">{hint}</p>
    </button>
  )
}

function BriefField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-0.5 text-sm text-slate-800 break-words">{value}</div>
    </div>
  )
}

function CheckRow({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone: 'ok' | 'warn' | 'bad'
}) {
  const dot = tone === 'ok' ? 'bg-green-500' : tone === 'bad' ? 'bg-red-500' : 'bg-orange-500'
  const text = tone === 'ok' ? 'text-green-700' : tone === 'bad' ? 'text-red-700' : 'text-orange-700'
  return (
    <div className="flex items-start gap-3">
      <span className={`mt-1.5 inline-block w-2 h-2 rounded-full ${dot}`} />
      <div className="flex-1">
        <div className="text-xs text-slate-500">{label}</div>
        <div className={`text-sm font-medium ${text}`}>{value}</div>
      </div>
    </div>
  )
}
