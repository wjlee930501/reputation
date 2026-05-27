'use client'

import { useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { fetchAPI } from '@/lib/api'
import {
  DAYS,
  DEFAULT_PUBLISH_DAYS_BY_PLAN,
  firstDayOfNextMonthInputValue,
  validateScheduleCapacity,
} from '@/lib/schedule'

export default function SchedulePage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const [plan, setPlan] = useState('PLAN_16')
  const [selectedDays, setSelectedDays] = useState<number[]>(DEFAULT_PUBLISH_DAYS_BY_PLAN.PLAN_16)
  const [activeFrom, setActiveFrom] = useState(firstDayOfNextMonthInputValue())
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
    const capacityError = validateScheduleCapacity(plan, selectedDays, activeFrom)
    if (capacityError) {
      setError(capacityError)
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
      <h2 className="text-xl font-bold text-slate-900 mb-2">콘텐츠 운영 스케줄</h2>
      <p className="text-sm text-slate-600 mb-6">
        병원 콘텐츠 허브에 발행할 월간 콘텐츠 수와 운영 요일을 설정합니다.
      </p>

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
          <div className="flex gap-3 mt-4">
            <button
              onClick={() => router.push(`/hospitals/${id}/content`)}
              className="px-4 py-2 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition-colors"
            >
              콘텐츠 확인하기
            </button>
            <button
              onClick={() => router.push(`/hospitals/${id}/dashboard`)}
              className="px-4 py-2 border border-green-300 text-green-800 text-sm font-medium rounded-lg hover:bg-green-100 transition-colors"
            >
              대시보드로 돌아가기
            </button>
          </div>
        </div>
      ) : (
        <form onSubmit={handleSubmit} className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-6">
          {/* 월간 운영량 */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-2">월간 운영량</label>
            <select
              value={plan}
              onChange={(e) => {
                const nextPlan = e.target.value
                setPlan(nextPlan)
                setSelectedDays(DEFAULT_PUBLISH_DAYS_BY_PLAN[nextPlan] ?? [])
                setResult(null)
                setError(null)
              }}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
            >
              <option value="PLAN_16">월 16편 집중 운영</option>
              <option value="PLAN_12">월 12편 표준 운영</option>
              <option value="PLAN_8">월 8편 기본 운영</option>
            </select>
          </div>

          {/* 발행 요일 */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-2">발행 요일</label>
            <div className="flex gap-2">
              {DAYS.map((day, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => toggleDay(idx)}
                  className={`w-10 h-10 rounded-full text-sm font-medium transition-colors ${
                    selectedDays.includes(idx)
                      ? 'bg-blue-600 text-white'
                      : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                  }`}
                >
                  {day}
                </button>
              ))}
            </div>
            <p className="text-xs text-slate-500 mt-2">
              선택된 요일: {selectedDays.map((d) => DAYS[d]).join(', ') || '없음'}
            </p>
          </div>

          {/* 시작일 */}
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-2">시작일</label>
            <input
              type="date"
              value={activeFrom}
              onChange={(e) => setActiveFrom(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
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
