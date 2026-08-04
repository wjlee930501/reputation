'use client'

import { useCallback, useEffect, useState } from 'react'

import { ApiError, fetchAPI } from '@/lib/api'
import { fetchCurrentAccount } from '@/lib/current-account'

type CostGuardCategory = {
  category: string
  label: string
  daily_used: number
  daily_limit: number
  daily_limit_default: number
  monthly_used: number
  monthly_limit: number
  daily_actual: number
  monthly_actual: number
}

type CostGuardStatus = {
  enabled: boolean
  kill_switch_active: boolean
  categories: CostGuardCategory[]
}

// 백엔드 cost_guard.MAX_DAILY_LIMIT_MULTIPLIER와 같은 값. 화면에서 미리 안내하기 위한
// 표시용이고, 실제 강제는 백엔드가 한다.
const MAX_DAILY_MULTIPLIER = 3

function errorMessage(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message
  if (e instanceof Error) return e.message
  return fallback
}

function usageRatio(used: number, limit: number): number {
  if (limit <= 0) return 0
  return Math.min(1, used / limit)
}

function barTone(ratio: number): string {
  if (ratio >= 1) return 'bg-red-500'
  if (ratio >= 0.8) return 'bg-amber-500'
  return 'bg-blue-500'
}

// 예약(작업 수)과 실제(공급자 호출 수)를 나란히 보여준다. 이미지 1건은 OpenAI 최대 3회 +
// Imagen 1회까지 가므로 둘이 벌어지는 것이 정상이고, 그 배수가 곧 "상한이 실제 지출을
// 얼마나 과소평가하고 있는가"다.
function UsageBar({
  label,
  used,
  limit,
  actual,
}: {
  label: string
  used: number
  limit: number
  actual: number
}) {
  const ratio = usageRatio(used, limit)
  const unlimited = limit <= 0
  const amplification = used > 0 ? actual / used : 0
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-slate-500">{label}</span>
        <span className="tabular-nums text-slate-700">
          {unlimited ? `${used}건 (상한 없음)` : `${used} / ${limit}건`}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
        <div className={`h-full ${barTone(ratio)}`} style={{ width: `${Math.round(ratio * 100)}%` }} />
      </div>
      <p className="mt-1 text-[11px] tabular-nums text-slate-500">
        실제 호출 {actual}건
        {amplification > 1.2 && (
          <span className="ml-1 rounded bg-amber-100 px-1 py-0.5 font-medium text-amber-800">
            재시도 {amplification.toFixed(1)}배
          </span>
        )}
      </p>
    </div>
  )
}

export default function OperationsPage() {
  const [status, setStatus] = useState<CostGuardStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [actionSuccess, setActionSuccess] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [raiseTarget, setRaiseTarget] = useState<CostGuardCategory | null>(null)
  const [raiseValue, setRaiseValue] = useState('')

  // 신원을 모르면 상향 버튼을 감춘다 — 지출을 늘리는 조작이라 fail-closed가 맞다.
  const [role, setRole] = useState<string | null>(null)
  const canRaiseLimit = role === 'OWNER'

  useEffect(() => {
    void fetchCurrentAccount().then((account) => setRole(account?.role ?? null))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      setStatus(await fetchAPI<CostGuardStatus>('/admin/operations/cost-guard'))
    } catch (e) {
      setLoadError(errorMessage(e, '비용 사용량을 불러오지 못했습니다.'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function resetBanners() {
    setActionError('')
    setActionSuccess('')
  }

  async function toggleKillSwitch(next: boolean) {
    resetBanners()
    setBusy('kill-switch')
    try {
      await fetchAPI('/admin/operations/cost-guard/kill-switch', {
        method: 'POST',
        body: JSON.stringify({ enabled: next }),
      })
      setActionSuccess(
        next
          ? '전체 중지를 켰습니다. 콘텐츠·이미지·측정 자동 호출이 모두 멈춥니다.'
          : '전체 중지를 껐습니다. 자동 호출이 다시 진행됩니다.',
      )
      await load()
    } catch (e) {
      setActionError(errorMessage(e, '전체 중지 상태를 바꾸지 못했습니다.'))
    } finally {
      setBusy(null)
    }
  }

  async function submitDailyLimit(category: string, limit: number | null) {
    resetBanners()
    setBusy(category)
    try {
      await fetchAPI('/admin/operations/cost-guard/daily-limit', {
        method: 'POST',
        body: JSON.stringify({ category, limit }),
      })
      setActionSuccess(
        limit === null
          ? '오늘치 상향을 해제하고 기본 상한으로 되돌렸습니다.'
          : `오늘 하루만 상한을 ${limit}건으로 올렸습니다. 내일 자동으로 원래 값으로 돌아갑니다.`,
      )
      setRaiseTarget(null)
      setRaiseValue('')
      await load()
    } catch (e) {
      setActionError(errorMessage(e, '상한을 바꾸지 못했습니다.'))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-8">
      <header>
        <p className="admin-eyebrow">운영 설정</p>
        <h1 className="title2 mt-1 text-slate-900">비용 사용량</h1>
        <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-600">
          콘텐츠 생성·이미지·AI 언급률 측정에 쓰인 자동 호출량입니다. 상한에 도달하면 그날의
          자동 생성이 건너뛰어지고 Slack으로 알림이 갑니다. 급할 때는 오늘 하루치 상한만 올려
          막힌 작업을 풀 수 있습니다.
        </p>
      </header>

      {actionError && (
        <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {actionError}
        </p>
      )}
      {actionSuccess && (
        <p role="status" className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          {actionSuccess}
        </p>
      )}

      {loading ? (
        <p className="mt-6 rounded-xl border border-slate-200 bg-white px-6 py-10 text-center text-sm text-slate-500">
          불러오는 중...
        </p>
      ) : loadError ? (
        <div className="mt-6 rounded-xl border border-slate-200 bg-white px-6 py-10 text-center">
          <p role="alert" className="text-sm text-red-600">{loadError}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="mt-3 rounded-lg border border-slate-300 px-3 py-1.5 text-sm text-slate-700"
          >
            다시 시도
          </button>
        </div>
      ) : status ? (
        <>
          <section className="admin-panel mt-6 p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="title3 text-slate-900">전체 중지</h2>
                <p className="mt-1 text-sm text-slate-600">
                  {status.kill_switch_active
                    ? '지금 모든 자동 호출이 중지돼 있습니다. 콘텐츠가 생성되지 않습니다.'
                    : '자동 호출이 정상 진행 중입니다. 비용이 급증할 때만 중지하세요.'}
                </p>
                {!status.enabled && (
                  <p className="mt-1 text-xs text-amber-700">
                    비용 가드가 꺼져 있어 상한이 적용되지 않습니다(설정값 COST_GUARD_ENABLED).
                  </p>
                )}
              </div>
              <button
                type="button"
                disabled={busy === 'kill-switch'}
                onClick={() => void toggleKillSwitch(!status.kill_switch_active)}
                className={`shrink-0 rounded-lg px-4 py-2 text-sm font-semibold text-white disabled:opacity-50 ${
                  status.kill_switch_active ? 'bg-emerald-600 hover:bg-emerald-700' : 'bg-red-600 hover:bg-red-700'
                }`}
              >
                {busy === 'kill-switch'
                  ? '처리 중...'
                  : status.kill_switch_active
                    ? '중지 해제'
                    : '전체 중지'}
              </button>
            </div>
          </section>

          <div className="mt-6 space-y-4">
            {status.categories.map((c) => {
              const raised = c.daily_limit > c.daily_limit_default
              const dailyBlocked = c.daily_limit > 0 && c.daily_used >= c.daily_limit
              const ceiling = c.daily_limit_default * MAX_DAILY_MULTIPLIER
              return (
                <section key={c.category} className="admin-panel p-5">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="title3 text-slate-900">{c.label}</h3>
                      {raised && (
                        <p className="mt-1 text-xs text-violet-700">
                          오늘만 {c.daily_limit_default} → {c.daily_limit}건으로 올려둔 상태입니다. 내일 자동 원복됩니다.
                        </p>
                      )}
                      {dailyBlocked && !raised && (
                        <p className="mt-1 text-xs text-red-700">
                          오늘 일일 상한에 도달했습니다. 남은 자동 생성이 건너뛰어집니다.
                        </p>
                      )}
                    </div>
                    {canRaiseLimit && c.daily_limit_default > 0 && (
                      <div className="flex gap-2">
                        <button
                          type="button"
                          disabled={busy === c.category}
                          onClick={() => {
                            resetBanners()
                            setRaiseTarget(c)
                            setRaiseValue(String(Math.min(ceiling, c.daily_limit_default * 2)))
                          }}
                          className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                        >
                          오늘만 상한 올리기
                        </button>
                        {raised && (
                          <button
                            type="button"
                            disabled={busy === c.category}
                            onClick={() => void submitDailyLimit(c.category, null)}
                            className="rounded-lg border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                          >
                            원복
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                  <div className="mt-4 grid gap-4 sm:grid-cols-2">
                    <UsageBar
                      label="오늘"
                      used={c.daily_used}
                      limit={c.daily_limit}
                      actual={c.daily_actual}
                    />
                    <UsageBar
                      label="이번 달"
                      used={c.monthly_used}
                      limit={c.monthly_limit}
                      actual={c.monthly_actual}
                    />
                  </div>
                </section>
              )
            })}
          </div>

          <p className="mt-6 text-xs leading-relaxed text-slate-500">
            상한은 <strong>예약된 작업 수</strong>를 기준으로 걸립니다. 실패해 재시도되면 실제 외부 호출은
            그보다 많아지므로(이미지 1건은 최대 4회) 두 숫자를 함께 보여줍니다. &lsquo;재시도 N배&rsquo; 배지가
            자주 보이면 상한이 실제 지출을 그만큼 과소평가하고 있다는 뜻입니다. 시스템 장애로 상한 확인
            자체가 불가능할 때는 작업이 멈추지 않고 그대로 진행됩니다(가용성 우선).
            월간 상한은 화면에서 바꿀 수 없습니다 — 늘려야 한다면 담당 개발자와 상의해 설정을 바꿉니다.
            오늘치 상향은 기본값의 {MAX_DAILY_MULTIPLIER}배까지만 가능합니다.
          </p>
        </>
      ) : null}

      {raiseTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
          <form
            onSubmit={(e) => {
              e.preventDefault()
              const parsed = Number.parseInt(raiseValue, 10)
              if (Number.isFinite(parsed)) void submitDailyLimit(raiseTarget.category, parsed)
            }}
            className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl"
          >
            <h2 className="title3 text-slate-900">오늘 하루만 상한 올리기</h2>
            <p className="mt-1 text-sm text-slate-600">
              <strong>{raiseTarget.label}</strong>의 오늘 상한을 올립니다. 이번 달 전체 상한은 그대로이고,
              내일이 되면 기본값 {raiseTarget.daily_limit_default}건으로 자동으로 돌아갑니다.
            </p>
            <label className="mt-4 block text-sm">
              <span className="font-medium text-slate-700">오늘 상한 (건)</span>
              <input
                type="number"
                required
                autoFocus
                min={raiseTarget.daily_limit_default + 1}
                max={raiseTarget.daily_limit_default * MAX_DAILY_MULTIPLIER}
                value={raiseValue}
                onChange={(e) => setRaiseValue(e.target.value)}
                className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2"
              />
              <span className="mt-1 block text-xs text-slate-500">
                {raiseTarget.daily_limit_default + 1} ~ {raiseTarget.daily_limit_default * MAX_DAILY_MULTIPLIER}건 사이
              </span>
            </label>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setRaiseTarget(null)
                  setRaiseValue('')
                }}
                className="rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-700"
              >
                취소
              </button>
              <button
                type="submit"
                disabled={busy === raiseTarget.category}
                className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {busy === raiseTarget.category ? '적용 중...' : '올리기'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
