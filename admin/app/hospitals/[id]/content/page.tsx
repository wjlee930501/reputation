'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import Image from 'next/image'
import ReactMarkdown from 'react-markdown'
import { fetchAPI } from '@/lib/api'
import { AIQueryTarget, ContentItem, ExposureAction, TYPE_LABELS } from '@/types'

const ESSENCE_LABELS: Record<string, { label: string; color: string }> = {
  ALIGNED: { label: '운영 기준 통과', color: 'bg-green-100 text-green-700' },
  NEEDS_ESSENCE_REVIEW: { label: '운영 기준 재검토', color: 'bg-orange-100 text-orange-700' },
  MISSING_APPROVED_PHILOSOPHY: { label: '운영 기준 없음', color: 'bg-red-100 text-red-700' },
}

const ESSENCE_FALLBACK = { label: '미검수', color: 'bg-gray-100 text-gray-500' }

type BriefStatus = 'DRAFT' | 'APPROVED' | 'NEEDS_REVIEW'

const BRIEF_LABELS: Record<BriefStatus, { label: string; color: string }> = {
  DRAFT: { label: '콘텐츠 가이드 작성중', color: 'bg-gray-100 text-gray-600' },
  APPROVED: { label: '콘텐츠 가이드 승인', color: 'bg-green-100 text-green-700' },
  NEEDS_REVIEW: { label: '콘텐츠 가이드 재검토', color: 'bg-orange-100 text-orange-700' },
}

const BRIEF_FALLBACK = { label: '콘텐츠 가이드 없음', color: 'bg-gray-50 text-gray-400 border border-gray-200' }

// 프론트엔드 미리보기용 금지 표현 목록 (단순 포함 검사)
// 정식 정의는 backend/app/utils/medical_filter.py의 FORBIDDEN_EXPRESSIONS를 따름
// 변형(예: "최고의", "유일한")은 백엔드 저장 시 정규식으로 최종 검증됨
const FORBIDDEN = [
  '1등', '최고', '최우수', '유일', '완치', '100%',
  '성공률', '부작용 없는', '검증된', '가장 잘하는',
  '국내 최초', '세계 최초', '특허', '독보적',
]

function checkForbidden(text: string): string[] {
  return FORBIDDEN.filter((expr) => text.includes(expr))
}

function highlightForbidden(text: string, violations: string[]): string {
  if (violations.length === 0) return text
  let result = text
  for (const v of violations) {
    result = result.split(v).join(`【${v}】`)
  }
  return result
}

function formatBriefValue(value: unknown): string {
  if (value == null || value === '') return '미작성'
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

type ReviewStateKey = 'publishable' | 'needsReview' | 'notGenerated' | 'published' | 'rejected'

interface ReviewState {
  key: ReviewStateKey
  label: string
  badge: string
  reason?: string
  publishable: boolean
}

function getReviewState(item: ContentItem): ReviewState {
  if (item.status === 'PUBLISHED') {
    return { key: 'published', label: '발행 완료', badge: 'bg-blue-100 text-blue-700', publishable: false }
  }
  if (item.status === 'REJECTED') {
    return { key: 'rejected', label: '반려됨', badge: 'bg-red-100 text-red-700', reason: '야간 재생성 대기', publishable: false }
  }
  if (!item.title || !item.body) {
    return { key: 'notGenerated', label: '생성 전', badge: 'bg-gray-100 text-gray-500', reason: '야간 자동 생성 대기', publishable: false }
  }
  if (item.essence_status !== 'ALIGNED') {
    const reason =
      item.essence_status === 'NEEDS_ESSENCE_REVIEW' ? '운영 기준 재검토 필요' :
      item.essence_status === 'MISSING_APPROVED_PHILOSOPHY' ? '승인된 운영 기준 없음' :
      '운영 기준 미검수'
    return { key: 'needsReview', label: '검토 필요', badge: 'bg-orange-100 text-orange-700', reason, publishable: false }
  }
  return { key: 'publishable', label: '발행 가능', badge: 'bg-green-100 text-green-700', publishable: true }
}

export default function ContentPage() {
  const { id } = useParams<{ id: string }>()

  // Month filter
  const [year, setYear] = useState(new Date().getFullYear())
  const [month, setMonth] = useState(new Date().getMonth() + 1)

  // List state
  const [items, setItems] = useState<ContentItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [queryTargets, setQueryTargets] = useState<AIQueryTarget[]>([])
  const [exposureActions, setExposureActions] = useState<ExposureAction[]>([])

  // Detail / edit modal
  const [selected, setSelected] = useState<ContentItem | null>(null)
  const [actionLoading, setActionLoading] = useState(false)
  const [briefEditMode, setBriefEditMode] = useState(false)
  const [briefQueryTargetId, setBriefQueryTargetId] = useState('')
  const [briefExposureActionId, setBriefExposureActionId] = useState('')
  const [briefStatus, setBriefStatus] = useState<BriefStatus>('DRAFT')
  const [briefApprovedBy, setBriefApprovedBy] = useState('AE')
  const [briefJson, setBriefJson] = useState('')
  const [briefSaving, setBriefSaving] = useState(false)
  const [briefError, setBriefError] = useState<string | null>(null)

  // Inline edit
  const [editMode, setEditMode] = useState(false)
  const [editTitle, setEditTitle] = useState('')
  const [editBody, setEditBody] = useState('')
  const [editMeta, setEditMeta] = useState('')
  const [violations, setViolations] = useState<string[]>([])
  const [editError, setEditError] = useState<string | null>(null)
  const [editSaving, setEditSaving] = useState(false)

  // Bulk selection
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [bulkProgress, setBulkProgress] = useState<string | null>(null)
  const [bulkError, setBulkError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setSelectedIds(new Set())
    fetchAPI(`/admin/hospitals/${id}/content?year=${year}&month=${month}`)
      .then(setItems)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [id, month, year])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    Promise.all([
      fetchAPI(`/admin/hospitals/${id}/query-targets`).catch(() => []),
      fetchAPI(`/admin/hospitals/${id}/exposure-actions?limit=20`).catch(() => []),
    ]).then(([targets, actions]) => {
      setQueryTargets(targets)
      setExposureActions(actions)
    })
  }, [id])

  // Per-item review state, computed once per render
  const reviewByItem = useMemo(() => {
    const map = new Map<string, ReviewState>()
    for (const item of items) map.set(item.id, getReviewState(item))
    return map
  }, [items])

  const summary = useMemo(() => {
    const totals = { publishable: 0, needsReview: 0, notGenerated: 0, published: 0, rejected: 0 }
    for (const item of items) {
      const rs = reviewByItem.get(item.id)
      if (rs) totals[rs.key]++
    }
    return totals
  }, [items, reviewByItem])

  const selectableIds = useMemo(
    () => items.filter((item) => reviewByItem.get(item.id)?.publishable).map((item) => item.id),
    [items, reviewByItem],
  )

  function toggleSelect(itemId: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(itemId)) next.delete(itemId)
      else next.add(itemId)
      return next
    })
  }

  function toggleSelectAll() {
    if (selectableIds.length > 0 && selectedIds.size === selectableIds.length) {
      setSelectedIds(new Set())
    } else {
      setSelectedIds(new Set(selectableIds))
    }
  }

  async function handleBulkPublish() {
    const ids = Array.from(selectedIds)
    if (ids.length === 0) return
    setBulkError(null)
    setBulkProgress(`0/${ids.length} 발행 완료...`)
    let done = 0
    for (const itemId of ids) {
      try {
        await fetchAPI(`/admin/hospitals/${id}/content/${itemId}/publish`, {
          method: 'POST',
          body: JSON.stringify({ published_by: 'AE' }),
        })
        done++
        setBulkProgress(`${done}/${ids.length} 발행 완료...`)
      } catch {
        setBulkError(`일부 콘텐츠 발행에 실패했습니다. (${done}/${ids.length} 완료)`)
        break
      }
    }
    setBulkProgress(null)
    load()
  }

  async function handlePublish(itemId: string) {
    setActionLoading(true)
    try {
      await fetchAPI(`/admin/hospitals/${id}/content/${itemId}/publish`, {
        method: 'POST',
        body: JSON.stringify({ published_by: 'AE' }),
      })
      load()
      setSelected(null)
    } catch (e: unknown) {
      setEditError(e instanceof Error ? e.message : '발행에 실패했습니다.')
    } finally {
      setActionLoading(false)
    }
  }

  async function handleReject(itemId: string) {
    if (!confirm('이 콘텐츠를 반려하시겠습니까? 야간에 재생성됩니다.')) return
    setActionLoading(true)
    try {
      await fetchAPI(`/admin/hospitals/${id}/content/${itemId}/reject`, { method: 'POST' })
      load()
      setSelected(null)
    } catch (e: unknown) {
      setEditError(e instanceof Error ? e.message : '반려에 실패했습니다.')
    } finally {
      setActionLoading(false)
    }
  }

  async function openDetail(item: ContentItem) {
    setEditMode(false)
    setBriefEditMode(false)
    setEditError(null)
    setBriefError(null)
    setViolations([])
    try {
      const full = await fetchAPI(`/admin/hospitals/${id}/content/${item.id}`)
      setSelected(full)
    } catch {
      setSelected(item)
    }
  }

  function enterEditMode() {
    if (!selected) return
    setEditTitle(selected.title ?? '')
    setEditBody(selected.body ?? '')
    setEditMeta(selected.meta_description ?? '')
    setViolations([])
    setEditError(null)
    setEditMode(true)
  }

  function enterBriefEditMode() {
    if (!selected) return
    setBriefQueryTargetId(selected.query_target_id ?? '')
    setBriefExposureActionId(selected.exposure_action_id ?? '')
    setBriefStatus((selected.brief_status as BriefStatus | null) ?? 'DRAFT')
    setBriefApprovedBy(selected.brief_approved_by ?? 'AE')
    setBriefJson(selected.content_brief ? JSON.stringify(selected.content_brief, null, 2) : '')
    setBriefError(null)
    setBriefEditMode(true)
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
    setEditSaving(true)
    setEditError(null)
    try {
      const updated = await fetchAPI(`/admin/hospitals/${id}/content/${selected.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          title: editTitle,
          body: editBody,
          meta_description: editMeta,
        }),
      })
      setSelected(updated)
      setEditMode(false)
      load()
    } catch (e: unknown) {
      if (e instanceof Error) {
        try {
          const parsed = JSON.parse(e.message)
          const detail = parsed.detail || parsed
          if (detail.violations) {
            setViolations(detail.violations)
            setEditError(`금지 표현: ${detail.violations.join(', ')}`)
          } else {
            setEditError(e.message)
          }
        } catch {
          setEditError(e.message)
        }
      } else {
        setEditError('저장에 실패했습니다.')
      }
    } finally {
      setEditSaving(false)
    }
  }

  async function handleSaveBrief() {
    if (!selected) return
    let parsedBrief: Record<string, unknown> | undefined
    const trimmed = briefJson.trim()
    if (trimmed) {
      try {
        const parsed = JSON.parse(trimmed)
        if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
          setBriefError('Brief JSON은 객체여야 합니다.')
          return
        }
        parsedBrief = parsed as Record<string, unknown>
      } catch {
        setBriefError('Brief JSON 형식이 올바르지 않습니다.')
        return
      }
    }

    setBriefSaving(true)
    setBriefError(null)
    try {
      const updated = await fetchAPI(`/admin/hospitals/${id}/content/${selected.id}/brief`, {
        method: 'PATCH',
        body: JSON.stringify({
          query_target_id: briefQueryTargetId || null,
          exposure_action_id: briefExposureActionId || null,
          brief_status: briefStatus,
          brief_approved_by: briefStatus === 'APPROVED' ? (briefApprovedBy || 'AE') : null,
          ...(parsedBrief ? { content_brief: parsedBrief } : { regenerate_brief: true }),
        }),
      })
      setSelected(updated)
      setBriefEditMode(false)
      load()
    } catch (e: unknown) {
      setBriefError(e instanceof Error ? e.message : 'Brief 저장에 실패했습니다.')
    } finally {
      setBriefSaving(false)
    }
  }

  const currentYear = new Date().getFullYear()
  const yearOptions = [currentYear - 1, currentYear, currentYear + 1]
  const monthOptions = Array.from({ length: 12 }, (_, i) => i + 1)

  const selectedReview = selected ? getReviewState(selected) : null
  const selectedTextViolations = selected
    ? checkForbidden(`${selected.title ?? ''} ${selected.body ?? ''} ${selected.meta_description ?? ''}`)
    : []
  const selectedFindings: string[] = Array.isArray(selected?.essence_check_summary?.findings)
    ? (selected!.essence_check_summary!.findings as unknown[]).map((f) => String(f))
    : []
  const selectedTarget = selected?.query_target_id
    ? queryTargets.find((target) => target.id === selected.query_target_id) ?? null
    : null
  const selectedAction = selected?.exposure_action_id
    ? exposureActions.find((action) => action.id === selected.exposure_action_id) ?? null
    : null

  return (
    <div className="p-8">
      {/* Hero / summary header */}
      <div className="mb-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">콘텐츠 검수 · 발행</h2>
            <p className="text-sm text-gray-500 mt-1">
              콘텐츠 운영 기준 통과 여부와 의료광고 리스크를 확인한 뒤 발행합니다.
            </p>
          </div>

          {/* Bulk action bar */}
          {selectedIds.size > 0 && (
            <div className="flex items-center gap-3 bg-white border border-gray-200 rounded-lg px-3 py-2 shadow-sm">
              {bulkProgress && (
                <span className="text-sm text-blue-600 font-medium">{bulkProgress}</span>
              )}
              {bulkError && (
                <span className="text-sm text-red-600">{bulkError}</span>
              )}
              <span className="text-sm text-gray-700">
                <span className="font-semibold">{selectedIds.size}개</span> 발행 대기 선택됨
              </span>
              <button
                onClick={handleBulkPublish}
                disabled={!!bulkProgress}
                className="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 disabled:opacity-50"
              >
                선택한 원고 발행
              </button>
            </div>
          )}
        </div>

        {/* Summary cards */}
        <div className="mt-5 grid grid-cols-2 md:grid-cols-4 gap-3">
          <SummaryCard label="발행 가능" value={summary.publishable} tone="green" hint="콘텐츠 운영 기준 통과 · 본문 완성" />
          <SummaryCard label="검토 필요" value={summary.needsReview} tone="orange" hint="콘텐츠 운영 기준 재검토 필요" />
          <SummaryCard label="생성 전" value={summary.notGenerated} tone="gray" hint="야간 자동 생성 대기" />
          <SummaryCard label="발행 완료" value={summary.published} tone="blue" hint="이번 달 누적" />
        </div>
      </div>

      {/* Month filter */}
      <div className="flex items-center gap-3 mb-4">
        <select
          value={year}
          onChange={(e) => setYear(Number(e.target.value))}
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {yearOptions.map((y) => (
            <option key={y} value={y}>{y}년</option>
          ))}
        </select>
        <select
          value={month}
          onChange={(e) => setMonth(Number(e.target.value))}
          className="px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
        >
          {monthOptions.map((m) => (
            <option key={m} value={m}>{m}월</option>
          ))}
        </select>
        <span className="text-xs text-gray-500 ml-1">
          콘텐츠 운영 기준을 통과한 초안만 일괄 선택할 수 있습니다.
        </span>
      </div>

      {loading && <div className="text-center py-16 text-gray-500">불러오는 중...</div>}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">오류: {error}</div>
      )}

      {!loading && !error && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="px-4 py-3 w-10">
                  <input
                    type="checkbox"
                    checked={selectableIds.length > 0 && selectedIds.size === selectableIds.length}
                    onChange={toggleSelectAll}
                    disabled={selectableIds.length === 0}
                    className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                </th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">발행예정일</th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">유형</th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">제목</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">순번</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">검수 상태</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">운영 기준</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">콘텐츠 가이드</th>
                <th className="text-right px-6 py-3 text-gray-600 font-medium">액션</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.length === 0 && (
                <tr>
                  <td colSpan={9} className="text-center py-12 text-gray-400 text-sm">
                    이번 달 콘텐츠가 아직 없습니다.
                    <br />
                    <span className="text-gray-500">
                      스케줄 탭에서 월간 슬롯을 만들거나 야간 생성 결과를 기다려 주세요.
                    </span>
                  </td>
                </tr>
              )}
              {items.map((item) => {
                const review = reviewByItem.get(item.id) ?? getReviewState(item)
                const essence = item.essence_status
                  ? ESSENCE_LABELS[item.essence_status] ?? { label: item.essence_status, color: 'bg-gray-100 text-gray-700' }
                  : ESSENCE_FALLBACK
                const brief = item.brief_status
                  ? BRIEF_LABELS[item.brief_status] ?? { label: item.brief_status, color: 'bg-gray-100 text-gray-700' }
                  : BRIEF_FALLBACK
                const isSelectable = review.publishable
                return (
                  <tr key={item.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-4">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(item.id)}
                        onChange={() => toggleSelect(item.id)}
                        disabled={!isSelectable}
                        className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500 disabled:opacity-30"
                      />
                    </td>
                    <td className="px-6 py-4 text-gray-600">{item.scheduled_date}</td>
                    <td className="px-6 py-4 text-gray-600">{TYPE_LABELS[item.content_type] ?? item.content_type}</td>
                    <td className="px-6 py-4">
                      <button onClick={() => openDetail(item)} className="text-blue-600 hover:underline text-left">
                        {item.title ?? <span className="text-gray-400 italic">생성 전</span>}
                      </button>
                    </td>
                    <td className="px-6 py-4 text-center text-gray-500">
                      {item.sequence_no}/{item.total_count}
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${review.badge}`}>
                        {review.label}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${essence.color}`}>
                        {essence.label}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${brief.color}`}>
                        {brief.label}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right">
                      {review.publishable ? (
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => handlePublish(item.id)}
                            disabled={actionLoading}
                            className="px-3 py-1 bg-green-600 text-white text-xs rounded hover:bg-green-700 disabled:opacity-50"
                          >
                            발행
                          </button>
                          <button
                            onClick={() => handleReject(item.id)}
                            disabled={actionLoading}
                            className="px-3 py-1 bg-red-100 text-red-700 text-xs rounded hover:bg-red-200 disabled:opacity-50"
                          >
                            반려
                          </button>
                        </div>
                      ) : review.key === 'needsReview' && item.title ? (
                        <button
                          onClick={() => openDetail(item)}
                          className="text-xs text-orange-700 hover:underline"
                        >
                          {review.reason ?? '검토 필요'} · 열기
                        </button>
                      ) : (
                        <span className="text-xs text-gray-400">{review.reason ?? '—'}</span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Detail / Edit Modal */}
      {selected && selectedReview && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className={`bg-white rounded-xl shadow-xl w-full max-h-[90vh] overflow-auto ${editMode || briefEditMode ? 'max-w-5xl' : 'max-w-2xl'}`}>
            <div className="flex items-center justify-between p-6 border-b border-gray-200">
              <div>
                <span className="text-xs font-medium text-gray-500 uppercase">
                  {TYPE_LABELS[selected.content_type] ?? selected.content_type}
                </span>
                {!editMode && (
                  <h3 className="text-lg font-bold text-gray-900 mt-0.5">{selected.title ?? '생성 전 슬롯'}</h3>
                )}
              </div>
              <div className="flex items-center gap-2">
                {!editMode && !briefEditMode && (
                  <button
                    onClick={enterBriefEditMode}
                    className="px-3 py-1.5 text-sm font-medium text-slate-700 border border-slate-300 rounded-lg hover:bg-slate-50"
                  >
                    콘텐츠 가이드 편집
                  </button>
                )}
                {!editMode && !briefEditMode && selected.status === 'DRAFT' && (
                  <button
                    onClick={enterEditMode}
                    className="px-3 py-1.5 text-sm font-medium text-blue-600 border border-blue-300 rounded-lg hover:bg-blue-50"
                  >
                    편집
                  </button>
                )}
                <button
                  onClick={() => { setSelected(null); setEditMode(false); setBriefEditMode(false) }}
                  className="text-gray-400 hover:text-gray-600 text-xl"
                >
                  ✕
                </button>
              </div>
            </div>

            {editError && (
              <div className="mx-6 mt-4 bg-red-50 border border-red-200 rounded-lg p-3 text-red-700 text-sm">
                {editError}
                {violations.length > 0 && (
                  <ul className="mt-1 list-disc list-inside text-xs">
                    {violations.map((v) => <li key={v}>{v}</li>)}
                  </ul>
                )}
              </div>
            )}
            {briefError && (
              <div className="mx-6 mt-4 bg-red-50 border border-red-200 rounded-lg p-3 text-red-700 text-sm">
                {briefError}
              </div>
            )}

            {briefEditMode ? (
              <div className="p-6 space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">연결할 환자 질문</label>
                    <select
                      value={briefQueryTargetId}
                      onChange={(e) => setBriefQueryTargetId(e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">연결 안 함</option>
                      {queryTargets.map((target) => (
                        <option key={target.id} value={target.id}>
                          {target.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">연결할 노출 보완 작업</label>
                    <select
                      value={briefExposureActionId}
                      onChange={(e) => setBriefExposureActionId(e.target.value)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">연결 안 함</option>
                      {exposureActions.map((action) => (
                        <option key={action.id} value={action.id}>
                          {action.title}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">콘텐츠 가이드 상태</label>
                    <select
                      value={briefStatus}
                      onChange={(e) => setBriefStatus(e.target.value as BriefStatus)}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="DRAFT">작성중</option>
                      <option value="APPROVED">승인</option>
                      <option value="NEEDS_REVIEW">재검토</option>
                    </select>
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">승인자</label>
                    <input
                      type="text"
                      value={briefApprovedBy}
                      onChange={(e) => setBriefApprovedBy(e.target.value)}
                      disabled={briefStatus !== 'APPROVED'}
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-50 disabled:text-gray-400"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">콘텐츠 가이드(JSON)</label>
                  <textarea
                    value={briefJson}
                    onChange={(e) => setBriefJson(e.target.value)}
                    rows={18}
                    placeholder="비워두면 연결한 환자 질문과 노출 보완 작업을 기준으로 콘텐츠 가이드 초안이 자동 생성됩니다."
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                  />
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={handleSaveBrief}
                    disabled={briefSaving}
                    className="px-5 py-2.5 bg-slate-900 text-white text-sm font-medium rounded-lg hover:bg-slate-800 disabled:opacity-50"
                  >
                    {briefSaving ? '저장 중...' : '콘텐츠 가이드 저장'}
                  </button>
                  <button
                    onClick={() => { setBriefEditMode(false); setBriefError(null) }}
                    className="px-5 py-2.5 bg-gray-100 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-200"
                  >
                    취소
                  </button>
                </div>
              </div>
            ) : editMode ? (
              /* Edit split view */
              <div className="p-6">
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">제목</label>
                  <input
                    type="text"
                    value={editTitle}
                    onChange={(e) => handleEditTitleChange(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">Meta Description</label>
                  <input
                    type="text"
                    value={editMeta}
                    onChange={(e) => handleEditMetaChange(e.target.value)}
                    maxLength={300}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  <p className="text-[11px] text-gray-400 mt-1 text-right">{editMeta.length}/300</p>
                </div>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-gray-700 mb-1.5">본문 (마크다운)</label>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <p className="text-xs text-gray-400 mb-1">편집</p>
                      <textarea
                        value={editBody}
                        onChange={(e) => handleEditBodyChange(e.target.value)}
                        rows={18}
                        className={`w-full px-3 py-2 border rounded-lg text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none ${violations.length > 0 ? 'border-red-400 bg-red-50' : 'border-gray-300'}`}
                      />
                      {violations.length > 0 && (
                        <p className="text-xs text-red-600 mt-1">
                          금지 표현: {violations.join(', ')}
                        </p>
                      )}
                    </div>
                    <div>
                      <p className="text-xs text-gray-400 mb-1">미리보기</p>
                      <div className="h-full border border-gray-200 rounded-lg p-3 overflow-auto bg-gray-50">
                        <div className="prose prose-sm max-w-none text-gray-700">
                          <ReactMarkdown>
                            {violations.length > 0 ? highlightForbidden(editBody, violations) : editBody}
                          </ReactMarkdown>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={handleSaveEdit}
                    disabled={editSaving || violations.length > 0}
                    className="px-5 py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50"
                  >
                    {editSaving ? '저장 중...' : '저장'}
                  </button>
                  <button
                    onClick={() => { setEditMode(false); setViolations([]); setEditError(null) }}
                    className="px-5 py-2.5 bg-gray-100 text-gray-700 text-sm font-medium rounded-lg hover:bg-gray-200"
                  >
                    취소
                  </button>
                </div>
              </div>
            ) : (
              /* Read mode */
              <div className="p-6">
                <div className="mb-5 border border-gray-200 rounded-lg overflow-hidden">
                  <div className="px-4 py-2 bg-gray-50 border-b border-gray-200 text-xs font-semibold text-gray-600 uppercase tracking-wide">
                    AI 노출 콘텐츠 가이드
                  </div>
                  <div className="p-4 space-y-3 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${
                        selected.brief_status
                          ? BRIEF_LABELS[selected.brief_status]?.color ?? 'bg-gray-100 text-gray-700'
                          : BRIEF_FALLBACK.color
                      }`}>
                        {selected.brief_status ? BRIEF_LABELS[selected.brief_status]?.label ?? selected.brief_status : BRIEF_FALLBACK.label}
                      </span>
                      {selected.brief_approved_at && (
                        <span className="text-xs text-gray-500">
                          {selected.brief_approved_by ?? 'AE'} · {selected.brief_approved_at}
                        </span>
                      )}
                    </div>
                    <BriefField label="연결된 환자 질문" value={selectedTarget?.name ?? selected.query_target_id ?? '미연결'} />
                    <BriefField label="연결된 노출 보완 작업" value={selectedAction?.title ?? selected.exposure_action_id ?? '미연결'} />
                    <BriefField label="타겟 질문" value={String(selected.content_brief?.target_query ?? '미작성')} />
                    <BriefField label="환자 의도" value={String(selected.content_brief?.patient_intent ?? '미작성')} />
                    <BriefField
                      label="내부 링크"
                      value={formatBriefValue(selected.content_brief?.internal_link_target)}
                    />
                    <BriefList label="반드시 담을 메시지" values={selected.content_brief?.must_use_messages} />
                    <BriefList label="피해야 할 표현" values={selected.content_brief?.avoid_messages} />
                    <BriefList label="의료광고 리스크 규칙" values={selected.content_brief?.medical_risk_rules} />
                  </div>
                </div>

                {/* 검수 체크 panel */}
                <div className="mb-5 border border-gray-200 rounded-lg overflow-hidden">
                  <div className="px-4 py-2 bg-gray-50 border-b border-gray-200 text-xs font-semibold text-gray-600 uppercase tracking-wide">
                    검수 체크
                  </div>
                  <div className="p-4 space-y-3 text-sm">
                    <CheckRow
                      label="콘텐츠 운영 기준"
                      value={
                        selected.essence_status
                          ? ESSENCE_LABELS[selected.essence_status]?.label ?? selected.essence_status
                          : '미검수'
                      }
                      tone={selected.essence_status === 'ALIGNED' ? 'ok' : 'warn'}
                    />
                    {selectedFindings.length > 0 && (
                      <ul className="list-disc list-inside text-xs text-gray-600 pl-2">
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
                      label={selectedReview.key === 'published' || selectedReview.key === 'rejected' ? '발행 상태' : '발행 가능 여부'}
                      value={
                        selectedReview.publishable
                          ? '발행 가능'
                          : `${selectedReview.label}${selectedReview.reason ? ` · ${selectedReview.reason}` : ''}`
                      }
                      tone={selectedReview.publishable || selectedReview.key === 'published' ? 'ok' : selectedReview.key === 'rejected' ? 'bad' : 'warn'}
                    />
                    {!selectedReview.publishable && selected.essence_status && selected.essence_status !== 'ALIGNED' && (
                      <p className="text-xs text-orange-700 bg-orange-50 border border-orange-200 rounded px-2 py-1.5">
                        콘텐츠 운영 기준 검토 후 발행 가능합니다.
                      </p>
                    )}
                  </div>
                </div>

                {selected.image_url && (
                  <div className="relative w-full h-48 rounded-lg overflow-hidden mb-4">
                    <Image src={selected.image_url} alt="" fill className="object-cover" />
                  </div>
                )}
                {selected.body && (
                  <div className="prose prose-sm max-w-none text-gray-700">
                    <ReactMarkdown>{selected.body}</ReactMarkdown>
                  </div>
                )}
              </div>
            )}

            {!editMode && !briefEditMode && selected.status === 'DRAFT' && selected.title && (
              <div className="p-6 border-t border-gray-200">
                {!selectedReview.publishable && (
                  <p className="text-xs text-gray-500 mb-2">
                    {selectedReview.reason ?? '발행 불가 상태입니다.'} — 발행 버튼이 비활성화되어 있습니다.
                  </p>
                )}
                <div className="flex gap-3">
                  <button
                    onClick={() => handlePublish(selected.id)}
                    disabled={actionLoading || !selectedReview.publishable}
                    className="flex-1 py-2.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    발행하기
                  </button>
                  <button
                    onClick={() => handleReject(selected.id)}
                    disabled={actionLoading}
                    className="flex-1 py-2.5 bg-red-100 text-red-700 text-sm font-medium rounded-lg hover:bg-red-200 disabled:opacity-50"
                  >
                    반려
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function SummaryCard({
  label,
  value,
  tone,
  hint,
}: {
  label: string
  value: number
  tone: 'green' | 'orange' | 'gray' | 'blue'
  hint: string
}) {
  const tones: Record<string, string> = {
    green: 'border-green-200 bg-green-50',
    orange: 'border-orange-200 bg-orange-50',
    gray: 'border-gray-200 bg-gray-50',
    blue: 'border-blue-200 bg-blue-50',
  }
  const numTones: Record<string, string> = {
    green: 'text-green-700',
    orange: 'text-orange-700',
    gray: 'text-gray-700',
    blue: 'text-blue-700',
  }
  return (
    <div className={`rounded-xl border ${tones[tone]} px-4 py-3`}>
      <p className="text-xs font-medium text-gray-600">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${numTones[tone]}`}>{value}</p>
      <p className="text-[11px] text-gray-500 mt-0.5">{hint}</p>
    </div>
  )
}

function BriefField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-0.5 text-sm text-gray-800 break-words">{value}</div>
    </div>
  )
}

function BriefList({ label, values }: { label: string; values: unknown }) {
  const items = Array.isArray(values) ? values.map((value) => formatBriefValue(value)).filter(Boolean) : []
  return (
    <div>
      <div className="text-xs text-gray-500">{label}</div>
      {items.length > 0 ? (
        <ul className="mt-0.5 list-disc list-inside text-sm text-gray-800 space-y-0.5">
          {items.map((item, idx) => (
            <li key={`${label}-${idx}`}>{item}</li>
          ))}
        </ul>
      ) : (
        <div className="mt-0.5 text-sm text-gray-400">미작성</div>
      )}
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
        <div className="text-xs text-gray-500">{label}</div>
        <div className={`text-sm font-medium ${text}`}>{value}</div>
      </div>
    </div>
  )
}
