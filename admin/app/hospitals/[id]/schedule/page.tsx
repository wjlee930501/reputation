'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { fetchAPI } from '@/lib/api'

const DAYS = ['월', '화', '수', '목', '금', '토', '일']

export default function SchedulePage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const [plan, setPlan] = useState('PLAN_16')
  const [selectedDays, setSelectedDays] = useState<number[]>([1, 4]) // 화, 금
  const [activeFrom, setActiveFrom] = useState(new Date().toISOString().split('T')[0])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ slots_created: number; first_publish_date: string } | null>(null)

  function toggleDay(idx: number) {
    setSelectedDays((prev) =>
      prev.includes(idx) ? prev.filter((d) => d !== idx) : [...prev, idx].sort()
    )
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (selectedDays.length === 0) {
      setError('발행 요일을 하나 이상 선택해 주세요.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await fetchAPI(`/admin/hospitals/${id}/schedule`, {
        method: 'POST',
        body: JSON.stringify({ plan, publish_days: selectedDays, active_from: activeFrom }),
      })
      setResult(data)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '저장에 실패했습니다.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-8 max-w-lg">
      <h2 className="text-xl font-bold text-gray-900 mb-6">콘텐츠 스케줄 설정</h2>

      {result ? (
        <div className="bg-green-50 border border-green-200 rounded-xl p-6">
          <p className="text-green-800 font-medium text-lg">스케줄 설정 완료</p>
          <p className="text-green-700 text-sm mt-2">
            {result.slots_created}개의 콘텐츠 슬롯이 생성되었습니다.
          </p>
          {result.first_publish_date && (
            <p className="text-green-700 text-sm mt-1">
              첫 발행 예정일: <strong>{result.first_publish_date}</strong>
            </p>
          )}
          <button
            onClick={() => router.push(`/hospitals/${id}/content`)}
            className="mt-4 px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700"
          >
            콘텐츠 목록 보기
          </button>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-6">
          {/* 요금제 */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">요금제</label>
            <select
              value={plan}
              onChange={(e) => setPlan(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
            >
              <option value="PLAN_16">PLAN_16 — 16편/월</option>
              <option value="PLAN_12">PLAN_12 — 12편/월</option>
              <option value="PLAN_8">PLAN_8 — 8편/월</option>
            </select>
          </div>

          {/* 발행 요일 */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">발행 요일</label>
            <div className="flex gap-2">
              {DAYS.map((day, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => toggleDay(idx)}
                  className={`w-10 h-10 rounded-full text-sm font-medium transition-colors ${
                    selectedDays.includes(idx)
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {day}
                </button>
              ))}
            </div>
            <p className="text-xs text-gray-500 mt-2">
              선택된 요일: {selectedDays.map((d) => DAYS[d]).join(', ') || '없음'}
            </p>
          </div>

          {/* 시작일 */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">시작일</label>
            <input
              type="date"
              value={activeFrom}
              onChange={(e) => setActiveFrom(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-red-700 text-sm">{error}</div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? '저장 중...' : '스케줄 저장 및 슬롯 생성'}
          </button>
        </form>
      )}
    </div>
  )
}
