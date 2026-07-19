'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ApiError, fetchAPI } from '@/lib/api'
import {
  DAYS,
  DEFAULT_PUBLISH_DAYS_BY_PLAN,
  firstDayOfNextMonthInputValue,
  validateScheduleCapacity,
} from '@/lib/schedule'
import { canSubmitSchedule } from '@/lib/operator-safety'
import { PLAN_LABELS, type ScheduleInfo } from '@/types'
import { useHospitalHeader } from '../hospital-context'

export default function SchedulePage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const { refetch: refetchHeader } = useHospitalHeader()
  const [plan, setPlan] = useState('PLAN_16')
  const [selectedDays, setSelectedDays] = useState<number[]>(DEFAULT_PUBLISH_DAYS_BY_PLAN.PLAN_16)
  const [activeFrom, setActiveFrom] = useState(firstDayOfNextMonthInputValue())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ slots_created: number; first_publish_date: string } | null>(null)

  // 현재 운영 중인 스케줄 — 404는 "아직 스케줄 없음"으로 처리
  const [existing, setExisting] = useState<ScheduleInfo | null>(null)
  const [existingLoading, setExistingLoading] = useState(true)
  const [existingError, setExistingError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setExistingLoading(true)
    fetchAPI<ScheduleInfo>(`/admin/hospitals/${id}/schedule`)
      .then((schedule) => {
        if (cancelled || !schedule) return
        setExisting(schedule)
        // 기존 스케줄로 폼을 미리 채워 실수로 다른 값으로 덮어쓰지 않게 한다.
        setPlan(schedule.plan)
        setSelectedDays([...schedule.publish_days].sort((a, b) => a - b))
        // 시작일은 미래 날짜만 이어받는다 — 과거 날짜로 재저장하면 지난 달 슬롯을 다시 만들게 된다.
        const today = new Date().toISOString().slice(0, 10)
        if (schedule.active_from && schedule.active_from >= today) {
          setActiveFrom(schedule.active_from)
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return
        if (e instanceof ApiError && e.status === 404) {
          setExisting(null) // 아직 설정된 스케줄 없음 — 정상 흐름
        } else {
          setExistingError(e instanceof Error ? e.message : '기존 스케줄을 확인하지 못했습니다.')
        }
      })
      .finally(() => {
        if (!cancelled) setExistingLoading(false)
      })
    return () => { cancelled = true }
  }, [id])

  function toggleDay(idx: number) {
    setSelectedDays((prev) =>
      prev.includes(idx) ? prev.filter((d) => d !== idx) : [...prev, idx].sort()
    )
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canSubmitSchedule(existingLoading, existingError)) {
      setError('기존 스케줄 상태를 확인한 뒤 다시 시도해 주세요.')
      return
    }
    if (selectedDays.length === 0) {
      setError('발행 요일을 하나 이상 선택해 주세요.')
      return
    }
    const capacityError = validateScheduleCapacity(plan, selectedDays, activeFrom)
    if (capacityError) {
      setError(capacityError)
      return
    }
    if (existing) {
      const confirmed = confirm('기존 스케줄이 교체되고 미발행 초안 슬롯이 재생성됩니다. 계속할까요?')
      if (!confirmed) return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await fetchAPI<{ slots_created: number; first_publish_date: string; publish_days: number[] }>(
        `/admin/hospitals/${id}/schedule`,
        {
          method: 'POST',
          body: JSON.stringify({ plan, publish_days: selectedDays, active_from: activeFrom }),
        },
      )
      setResult(data)
      setExisting({
        plan: plan as ScheduleInfo['plan'],
        publish_days: selectedDays,
        active_from: activeFrom,
        is_active: true,
      })
      void refetchHeader()
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

      {existingLoading && (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500">
          현재 운영 중인 스케줄을 확인하는 중...
        </div>
      )}
      {existingError && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          기존 스케줄을 확인하지 못했습니다. 저장 전 운영 상태를 다시 확인해 주세요. ({existingError})
        </div>
      )}
      {!existingLoading && existing && (
        <div className="mb-4 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3">
          <p className="text-sm font-semibold text-blue-900">현재 운영 중인 스케줄</p>
          <dl className="mt-2 space-y-1 text-sm text-blue-900">
            <div className="flex justify-between gap-2">
              <dt className="text-blue-700">월간 운영량</dt>
              <dd className="font-medium">{PLAN_LABELS[existing.plan] ?? existing.plan}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-blue-700">발행 요일</dt>
              <dd className="font-medium">
                {[...existing.publish_days].sort().map((d) => DAYS[d]).join(', ') || '-'}
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt className="text-blue-700">시작일</dt>
              <dd className="font-medium">{existing.active_from}</dd>
            </div>
          </dl>
          <p className="mt-2 text-xs text-blue-700">
            새로 저장하면 기존 스케줄이 교체되고, 아직 생성되지 않은 미발행 초안 슬롯이 재생성됩니다.
          </p>
        </div>
      )}
      {!existingLoading && !existing && !existingError && (
        <div className="mb-4 rounded-xl border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-600">
          아직 설정된 스케줄이 없습니다. 첫 스케줄을 저장하면 해당 월의 콘텐츠 슬롯이 자동 생성됩니다.
        </div>
      )}

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
            <label htmlFor="schedule-plan" className="block text-sm font-medium text-slate-700 mb-2">월간 운영량</label>
            <select
              id="schedule-plan"
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
            <span className="block text-sm font-medium text-slate-700 mb-2">발행 요일</span>
            <div className="flex gap-2">
              {DAYS.map((day, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => toggleDay(idx)}
                  aria-pressed={selectedDays.includes(idx)}
                  aria-label={`${day}요일 ${selectedDays.includes(idx) ? '선택됨' : '선택 안 됨'}`}
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
            <label htmlFor="schedule-active-from" className="block text-sm font-medium text-slate-700 mb-2">시작일</label>
            <input
              id="schedule-active-from"
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
            disabled={loading || !canSubmitSchedule(existingLoading, existingError)}
            className="w-full py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? '저장 중...' : existing ? '스케줄 교체 및 슬롯 재생성' : '스케줄 저장 및 슬롯 생성'}
          </button>
        </form>
      )}
    </div>
  )
}
