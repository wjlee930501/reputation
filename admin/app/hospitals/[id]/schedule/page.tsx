'use client'

import { useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { ApiError, fetchAPI } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import {
  DAYS,
  DEFAULT_PUBLISH_DAYS_BY_PLAN,
  firstDayOfNextMonthInputValue,
  moveScheduleMonth,
  scheduleMonthLabel,
  validateScheduleCapacity,
} from '@/lib/schedule'
import { canSubmitSchedule } from '@/lib/operator-safety'
import { PLAN_CONTRACT_LABELS, PLAN_LABELS, type ScheduleInfo } from '@/types'
import { useHospitalHeader } from '../hospital-context'

interface ReadinessCheck {
  key: string
  label: string
  passed: boolean
  next_action?: string | null
}

interface ScheduleReadiness {
  essence?: {
    processed_source_count?: number | null
    required_source_count?: number | null
    approved_philosophy_exists?: boolean | null
    source_stale?: boolean | null
  } | null
  checks?: ReadinessCheck[]
}

const PLAN_DISTRIBUTION: Record<string, Array<[string, number]>> = {
  PLAN_20: [['FAQ', 5], ['질환 가이드', 4], ['치료 안내', 4], ['원장 칼럼', 2], ['건강 정보', 2], ['지역 특화', 2], ['공지', 1]],
  PLAN_16: [['FAQ', 4], ['질환 가이드', 3], ['치료 안내', 3], ['원장 칼럼', 2], ['건강 정보', 2], ['지역 특화', 1], ['공지', 1]],
  PLAN_12: [['FAQ', 3], ['질환 가이드', 3], ['치료 안내', 2], ['원장 칼럼', 2], ['건강 정보', 1], ['지역 특화', 1]],
}

function contentReadinessBlockers(readiness: ScheduleReadiness | null): string[] {
  if (!readiness) return []
  const checkByKey = new Map((readiness.checks ?? []).map((check) => [check.key, check]))
  const essence = readiness.essence
  const blockers: string[] = []

  if ((essence?.required_source_count ?? 0) === 0) {
    blockers.push('병원 근거 자료를 1개 이상 추가해 주세요.')
  }
  for (const key of ['essence_sources', 'essence_philosophy', 'essence_freshness']) {
    const check = checkByKey.get(key)
    if (check && !check.passed) {
      blockers.push(check.next_action || `${check.label} 단계를 완료해 주세요.`)
    }
  }
  return Array.from(new Set(blockers))
}

export default function SchedulePage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const { refetch: refetchHeader } = useHospitalHeader()
  const [plan, setPlan] = useState('PLAN_12')
  const [selectedDays, setSelectedDays] = useState<number[]>(DEFAULT_PUBLISH_DAYS_BY_PLAN.PLAN_12)
  const [activeFrom, setActiveFrom] = useState(firstDayOfNextMonthInputValue())
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<{ slots_created: number; first_publish_date: string } | null>(null)
  const [confirmingReplacement, setConfirmingReplacement] = useState(false)

  // 현재 운영 중인 스케줄 — 404는 "아직 스케줄 없음"으로 처리
  const [existing, setExisting] = useState<ScheduleInfo | null>(null)
  const [existingLoading, setExistingLoading] = useState(true)
  const [existingError, setExistingError] = useState<string | null>(null)
  const [readiness, setReadiness] = useState<ScheduleReadiness | null>(null)
  const [readinessLoading, setReadinessLoading] = useState(true)
  const [readinessError, setReadinessError] = useState<string | null>(null)

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
          if (!isExpectedOperatorRequestFailure(e)) throw e
          setExistingError(safeOperatorError('onboarding', '운영 화면을 다시 불러 현재 콘텐츠 발행 일정을 확인하세요.'))
        }
      })
      .finally(() => {
        if (!cancelled) setExistingLoading(false)
      })
    return () => { cancelled = true }
  }, [id])

  useEffect(() => {
    let cancelled = false
    setReadinessLoading(true)
    fetchAPI<ScheduleReadiness>(`/admin/hospitals/${id}/readiness`)
      .then((value) => {
        if (!cancelled) setReadiness(value)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        if (!isExpectedOperatorRequestFailure(e)) throw e
        setReadinessError(safeOperatorError('onboarding', '운영 준비도를 다시 불러 근거 자료와 콘텐츠 운영 기준 상태를 확인하세요.'))
      })
      .finally(() => {
        if (!cancelled) setReadinessLoading(false)
      })
    return () => { cancelled = true }
  }, [id])

  const readinessBlockers = contentReadinessBlockers(readiness)
  const canSaveSchedule = canSubmitSchedule(existingLoading, existingError)
    && !readinessLoading
    && !readinessError
    && readinessBlockers.length === 0

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
    if (readinessLoading || readinessError) {
      setError(readinessError ?? '운영 준비도를 확인한 뒤 다시 시도해 주세요.')
      return
    }
    if (readinessBlockers.length > 0) {
      setError(`콘텐츠 스케줄 설정 전 필요한 작업이 남아 있습니다.\n- ${readinessBlockers.join('\n- ')}`)
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
      setConfirmingReplacement(true)
      return
    }
    await saveSchedule()
  }

  async function saveSchedule() {
    setConfirmingReplacement(false)
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
      if (!isExpectedOperatorRequestFailure(e)) throw e
      setError(safeOperatorError('onboarding', '운영량과 발행 요일을 확인한 뒤 ‘저장’을 다시 누르세요.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-5xl p-4 sm:p-6 lg:p-8">
      <h2 className="text-xl font-bold text-slate-900 mb-2">콘텐츠 운영 스케줄</h2>
      <p className="text-sm text-slate-600 mb-6">
        병원 콘텐츠 허브에 발행할 월간 콘텐츠 수와 운영 요일을 설정합니다.
      </p>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="min-w-0">
      {existingLoading && (
        <div className="mb-4 rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500">
          현재 운영 중인 스케줄을 확인하는 중...
        </div>
      )}
      {existingError && (
        <div className="mb-4"><OperatorIssuePanel message={existingError} surface="onboarding" onRetry={() => window.location.reload()} retryLabel="콘텐츠 발행 일정 다시 불러오기" /></div>
      )}
      {readinessError && (
        <div className="mb-4"><OperatorIssuePanel message={readinessError} surface="onboarding" onRetry={() => window.location.reload()} retryLabel="운영 준비도 다시 불러오기" /></div>
      )}
      {!readinessLoading && readinessBlockers.length > 0 && (
        <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3">
          <p className="text-sm font-semibold text-amber-900">스케줄 설정 전 완료할 작업</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-900">
            {readinessBlockers.map((blocker) => (
              <li key={blocker}>{blocker}</li>
            ))}
          </ul>
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
        <form onSubmit={handleSubmit} className="bg-white rounded-xl border border-slate-200 p-4 space-y-6 sm:p-6">
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
              <option value="PLAN_12">{PLAN_CONTRACT_LABELS.PLAN_12}</option>
              <option value="PLAN_16">{PLAN_CONTRACT_LABELS.PLAN_16}</option>
              <option value="PLAN_20">{PLAN_CONTRACT_LABELS.PLAN_20}</option>
            </select>
          </div>

          {/* 발행 요일 */}
          <div>
            <span className="block text-sm font-medium text-slate-700 mb-2">발행 요일</span>
            <div className="grid grid-cols-7 gap-1.5 sm:gap-2">
              {DAYS.map((day, idx) => (
                <button
                  key={idx}
                  type="button"
                  onClick={() => toggleDay(idx)}
                  aria-pressed={selectedDays.includes(idx)}
                  aria-label={`${day}요일 ${selectedDays.includes(idx) ? '선택됨' : '선택 안 됨'}`}
                  className={`aspect-square min-h-10 w-full rounded-full text-sm font-medium transition-colors ${
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
            <div className="mb-2 flex items-center justify-between rounded-lg border border-slate-200 bg-slate-50 p-1" aria-label="스케줄 연월 이동">
              <button
                type="button"
                onClick={() => setActiveFrom((current) => moveScheduleMonth(current, -1))}
                className="min-h-10 rounded-md px-3 text-sm font-semibold text-slate-600 hover:bg-white"
                aria-label="이전 달"
              >
                ←
              </button>
              <output className="text-sm font-semibold tabular-nums text-slate-800">
                {scheduleMonthLabel(activeFrom)}
              </output>
              <button
                type="button"
                onClick={() => setActiveFrom((current) => moveScheduleMonth(current, 1))}
                className="min-h-10 rounded-md px-3 text-sm font-semibold text-slate-600 hover:bg-white"
                aria-label="다음 달"
              >
                →
              </button>
            </div>
            <input
              id="schedule-active-from"
              type="date"
              value={activeFrom}
              onChange={(e) => setActiveFrom(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          {error && (
            <OperatorIssuePanel message={error} surface="onboarding" />
          )}

          <button
            type="submit"
            disabled={loading || !canSaveSchedule}
            className="w-full py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? '저장 중...' : existing ? '스케줄 교체 및 슬롯 재생성' : '스케줄 저장 및 슬롯 생성'}
          </button>
        </form>
      )}
      </div>

      <aside className="rounded-xl border border-slate-200 bg-slate-50 p-4 lg:sticky lg:top-6 sm:p-5">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-slate-500">운영 미리보기</p>
        <h3 className="mt-2 text-base font-semibold text-slate-900">{PLAN_LABELS[plan as keyof typeof PLAN_LABELS] ?? plan}</h3>
        <p className="mt-1 text-sm text-slate-600">
          {activeFrom || '시작일 미정'}부터 {selectedDays.length > 0 ? selectedDays.map((day) => `${DAYS[day]}요일`).join(' · ') : '발행 요일 미정'}에 운영합니다.
        </p>

        <div className="mt-5 grid grid-cols-7 gap-1" aria-label="선택된 발행 요일">
          {DAYS.map((day, idx) => (
            <div key={day} className="text-center">
              <span className={`mx-auto flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold ${selectedDays.includes(idx) ? 'bg-blue-600 text-white' : 'bg-white text-slate-400'}`}>
                {day}
              </span>
            </div>
          ))}
        </div>

        <div className="mt-5 border-t border-slate-200 pt-4">
          <p className="text-sm font-semibold text-slate-800">월간 콘텐츠 구성</p>
          <dl className="mt-3 space-y-2">
            {(PLAN_DISTRIBUTION[plan] ?? []).map(([label, count]) => (
              <div key={label} className="flex items-center justify-between gap-3 text-sm">
                <dt className="text-slate-600">{label}</dt>
                <dd className="font-semibold tabular-nums text-slate-900">{count}편</dd>
              </div>
            ))}
          </dl>
        </div>

        <p className="mt-5 rounded-lg bg-white px-3 py-2.5 text-xs leading-5 text-slate-600">
          저장 시 선택한 시작월의 슬롯이 생성됩니다. 운영 중인 스케줄을 바꾸면 미발행 슬롯만 새 기준으로 재생성됩니다.
        </p>
      </aside>
      </div>

      {confirmingReplacement && existing && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4" role="presentation">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="schedule-replacement-title"
            className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-xl"
          >
            <h3 id="schedule-replacement-title" className="text-lg font-bold text-slate-900">
              기존 스케줄을 교체할까요?
            </h3>
            <p className="mt-2 text-sm leading-6 text-slate-600">
              저장하면 기존 설정이 아래 새 기준으로 바뀌고 미발행 초안 슬롯이 재생성됩니다.
              이 변경은 자동으로 되돌릴 수 없습니다.
            </p>
            <dl className="mt-4 divide-y divide-slate-100 rounded-xl border border-slate-200 px-4 text-sm">
              <div className="grid grid-cols-[100px_1fr] gap-3 py-3">
                <dt className="text-slate-500">월간 운영량</dt>
                <dd className="font-medium text-slate-900">
                  {PLAN_LABELS[existing.plan] ?? existing.plan} → {PLAN_LABELS[plan as keyof typeof PLAN_LABELS] ?? plan}
                </dd>
              </div>
              <div className="grid grid-cols-[100px_1fr] gap-3 py-3">
                <dt className="text-slate-500">발행 요일</dt>
                <dd className="font-medium text-slate-900">
                  {[...existing.publish_days].sort().map((day) => DAYS[day]).join(', ') || '없음'} →{' '}
                  {selectedDays.map((day) => DAYS[day]).join(', ') || '없음'}
                </dd>
              </div>
              <div className="grid grid-cols-[100px_1fr] gap-3 py-3">
                <dt className="text-slate-500">시작일</dt>
                <dd className="font-medium text-slate-900">{existing.active_from} → {activeFrom}</dd>
              </div>
            </dl>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmingReplacement(false)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                취소
              </button>
              <button
                type="button"
                onClick={() => void saveSchedule()}
                disabled={loading}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-50"
              >
                {loading ? '교체 중...' : '교체하고 슬롯 재생성'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
