'use client'

import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Image from 'next/image'
import ReactMarkdown from 'react-markdown'
import { fetchAPI } from '@/lib/api'
import { ContentItem, TYPE_LABELS } from '@/types'

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  DRAFT:     { label: '초안', color: 'bg-gray-100 text-gray-700' },
  READY:     { label: '대기', color: 'bg-yellow-100 text-yellow-700' },
  PUBLISHED: { label: '발행', color: 'bg-green-100 text-green-700' },
  REJECTED:  { label: '반려', color: 'bg-red-100 text-red-700' },
}

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

export default function ContentPage() {
  const { id } = useParams<{ id: string }>()

  // Month filter
  const [year, setYear] = useState(new Date().getFullYear())
  const [month, setMonth] = useState(new Date().getMonth() + 1)

  // List state
  const [items, setItems] = useState<ContentItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Detail / edit modal
  const [selected, setSelected] = useState<ContentItem | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

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

  // Bulk-selectable: DRAFT with body
  const selectableIds = items
    .filter((item) => item.status === 'DRAFT' && item.title && item.body)
    .map((item) => item.id)

  function toggleSelect(itemId: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(itemId)) next.delete(itemId)
      else next.add(itemId)
      return next
    })
  }

  function toggleSelectAll() {
    if (selectedIds.size === selectableIds.length) {
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
    setEditError(null)
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

  function handleEditBodyChange(val: string) {
    setEditBody(val)
    const found = checkForbidden(val + ' ' + editTitle)
    setViolations(found)
  }

  function handleEditTitleChange(val: string) {
    setEditTitle(val)
    const found = checkForbidden(editBody + ' ' + val)
    setViolations(found)
  }

  async function handleSaveEdit() {
    if (!selected) return
    const allText = editTitle + ' ' + editBody
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

  const currentYear = new Date().getFullYear()
  const yearOptions = [currentYear - 1, currentYear, currentYear + 1]
  const monthOptions = Array.from({ length: 12 }, (_, i) => i + 1)

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-bold text-gray-900">콘텐츠 관리</h2>

        {/* Bulk action bar */}
        {selectedIds.size > 0 && (
          <div className="flex items-center gap-3">
            {bulkProgress && (
              <span className="text-sm text-blue-600 font-medium">{bulkProgress}</span>
            )}
            {bulkError && (
              <span className="text-sm text-red-600">{bulkError}</span>
            )}
            <span className="text-sm text-gray-600">{selectedIds.size}개 선택</span>
            <button
              onClick={handleBulkPublish}
              disabled={!!bulkProgress}
              className="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 disabled:opacity-50"
            >
              선택 발행
            </button>
          </div>
        )}
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
                <th className="text-center px-6 py-3 text-gray-600 font-medium">상태</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">액션</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {items.length === 0 && (
                <tr>
                  <td colSpan={7} className="text-center py-12 text-gray-400">
                    해당 월에 콘텐츠가 없습니다.
                  </td>
                </tr>
              )}
              {items.map((item) => {
                const s = STATUS_LABELS[item.status] ?? { label: item.status, color: 'bg-gray-100 text-gray-700' }
                const isSelectable = item.status === 'DRAFT' && !!item.title && !!item.body
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
                      {item.title ? (
                        <button onClick={() => openDetail(item)} className="text-blue-600 hover:underline text-left">
                          {item.title}
                        </button>
                      ) : (
                        <span className="text-gray-400 italic">생성 전</span>
                      )}
                    </td>
                    <td className="px-6 py-4 text-center text-gray-500">
                      {item.sequence_no}/{item.total_count}
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${s.color}`}>
                        {s.label}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      {item.status === 'DRAFT' && item.title && (
                        <div className="flex items-center justify-center gap-2">
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
      {selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className={`bg-white rounded-xl shadow-xl w-full max-h-[90vh] overflow-auto ${editMode ? 'max-w-5xl' : 'max-w-2xl'}`}>
            <div className="flex items-center justify-between p-6 border-b border-gray-200">
              <div>
                <span className="text-xs font-medium text-gray-500 uppercase">
                  {TYPE_LABELS[selected.content_type] ?? selected.content_type}
                </span>
                {!editMode && (
                  <h3 className="text-lg font-bold text-gray-900 mt-0.5">{selected.title}</h3>
                )}
              </div>
              <div className="flex items-center gap-2">
                {!editMode && selected.status === 'DRAFT' && (
                  <button
                    onClick={enterEditMode}
                    className="px-3 py-1.5 text-sm font-medium text-blue-600 border border-blue-300 rounded-lg hover:bg-blue-50"
                  >
                    편집
                  </button>
                )}
                <button onClick={() => { setSelected(null); setEditMode(false) }} className="text-gray-400 hover:text-gray-600 text-xl">✕</button>
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

            {editMode ? (
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
                    onChange={(e) => setEditMeta(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
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

            {!editMode && selected.status === 'DRAFT' && selected.title && (
              <div className="flex gap-3 p-6 border-t border-gray-200">
                <button
                  onClick={() => handlePublish(selected.id)}
                  disabled={actionLoading}
                  className="flex-1 py-2.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 disabled:opacity-50"
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
            )}
          </div>
        </div>
      )}
    </div>
  )
}
