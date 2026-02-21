'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Image from 'next/image'
import { fetchAPI } from '@/lib/api'
import { ContentItem, TYPE_LABELS } from '@/types'

const STATUS_LABELS: Record<string, { label: string; color: string }> = {
  DRAFT:     { label: '초안', color: 'bg-gray-100 text-gray-700' },
  READY:     { label: '대기', color: 'bg-yellow-100 text-yellow-700' },
  PUBLISHED: { label: '발행', color: 'bg-green-100 text-green-700' },
  REJECTED:  { label: '반려', color: 'bg-red-100 text-red-700' },
}

export default function ContentPage() {
  const { id } = useParams<{ id: string }>()
  const [items, setItems] = useState<ContentItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<ContentItem | null>(null)
  const [actionLoading, setActionLoading] = useState(false)

  const load = () => {
    setLoading(true)
    fetchAPI(`/admin/hospitals/${id}/content`)
      .then(setItems)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [id])

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
      alert(e instanceof Error ? e.message : '발행에 실패했습니다.')
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
      alert(e instanceof Error ? e.message : '반려에 실패했습니다.')
    } finally {
      setActionLoading(false)
    }
  }

  async function openDetail(item: ContentItem) {
    try {
      const full = await fetchAPI(`/admin/hospitals/${id}/content/${item.id}`)
      setSelected(full)
    } catch {
      setSelected(item)
    }
  }

  return (
    <div className="p-8">
      <h2 className="text-xl font-bold text-gray-900 mb-6">콘텐츠 관리</h2>

      {loading && <div className="text-center py-16 text-gray-500">불러오는 중...</div>}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">오류: {error}</div>
      )}

      {!loading && !error && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
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
                  <td colSpan={6} className="text-center py-12 text-gray-400">
                    스케줄을 먼저 설정해 주세요.
                  </td>
                </tr>
              )}
              {items.map((item) => {
                const s = STATUS_LABELS[item.status] ?? { label: item.status, color: 'bg-gray-100 text-gray-700' }
                return (
                  <tr key={item.id} className="hover:bg-gray-50 transition-colors">
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

      {/* Detail Modal */}
      {selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[80vh] overflow-auto">
            <div className="flex items-center justify-between p-6 border-b border-gray-200">
              <div>
                <span className="text-xs font-medium text-gray-500 uppercase">
                  {TYPE_LABELS[selected.content_type] ?? selected.content_type}
                </span>
                <h3 className="text-lg font-bold text-gray-900 mt-0.5">{selected.title}</h3>
              </div>
              <button onClick={() => setSelected(null)} className="text-gray-400 hover:text-gray-600 text-xl">✕</button>
            </div>
            <div className="p-6">
              {selected.image_url && (
                <div className="relative w-full h-48 rounded-lg overflow-hidden mb-4">
                  <Image src={selected.image_url} alt="" fill className="object-cover" />
                </div>
              )}
              {selected.body && (
                <div className="prose prose-sm max-w-none text-gray-700 whitespace-pre-wrap">
                  {selected.body}
                </div>
              )}
            </div>
            {selected.status === 'DRAFT' && selected.title && (
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
