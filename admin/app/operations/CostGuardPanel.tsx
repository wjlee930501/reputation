'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { ApiError, fetchAPI } from '@/lib/api'

type CostCategory = {
  readonly category: string
  readonly label: string
  readonly daily_used: number | null
  readonly daily_limit: number
  readonly daily_limit_default: number
  readonly monthly_used: number | null
  readonly monthly_limit: number
  readonly daily_actual: number | null
  readonly monthly_actual: number | null
}

type CostStatus = {
  readonly availability?: 'AVAILABLE' | 'UNAVAILABLE'
  readonly enabled: boolean
  readonly kill_switch_active: boolean | null
  readonly categories: readonly CostCategory[]
}

function message(error: unknown): string {
  if (error instanceof ApiError || error instanceof Error) return error.message
  return '비용 설정을 처리하지 못했습니다.'
}

function ratio(used: number | null, limit: number): number {
  if (used === null) return 0
  return limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0
}

export function CostGuardPanel({ canRaiseLimit }: { readonly canRaiseLimit: boolean }) {
  const [status, setStatus] = useState<CostStatus | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const refreshingRef = useRef(false)

  const load = useCallback(async () => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    setRefreshing(true)
    try {
      const loaded = await fetchAPI<CostStatus>('/admin/operations/cost-guard')
      setStatus({ ...loaded, availability: loaded.availability ?? 'AVAILABLE' })
      setError('')
    } catch (caught) {
      setError(message(caught))
    } finally {
      refreshingRef.current = false
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  async function toggleKillSwitch() {
    if (!status || status.kill_switch_active === null) return
    setBusy('kill')
    setNotice('')
    try {
      await fetchAPI('/admin/operations/cost-guard/kill-switch', {
        method: 'POST', body: JSON.stringify({ enabled: !status.kill_switch_active }),
      })
      setNotice(status.kill_switch_active ? '자동 작업을 다시 시작했습니다.' : '모든 자동 작업을 중지했습니다.')
      await load()
    } catch (caught) {
      setError(message(caught))
    } finally {
      setBusy('')
    }
  }

  async function copyDeveloperContext() {
    try {
      await navigator.clipboard.writeText('화면: 운영 센터 > 비용·자동 작업 안전장치\n상태: 비용 기록을 확인할 수 없음\n요청: 비용 현황 연결 상태 확인')
      setNotice('개발팀 문의용 정보를 복사했습니다.')
    } catch (caught) {
      setError(message(caught))
    }
  }

  async function updateLimit(category: CostCategory, limit: number | null) {
    setBusy(category.category)
    setNotice('')
    try {
      await fetchAPI('/admin/operations/cost-guard/daily-limit', {
        method: 'POST', body: JSON.stringify({ category: category.category, limit }),
      })
      setNotice(limit === null ? '오늘 사용 한도를 기본값으로 되돌렸습니다.' : '오늘 하루 사용 한도를 올렸습니다.')
      await load()
    } catch (caught) {
      setError(message(caught))
    } finally {
      setBusy('')
    }
  }

  return (
    <details className="ops-cost-panel" open={status?.availability === 'UNAVAILABLE' || undefined}>
      <summary className="ops-control">
        <span><b>비용·자동 작업 안전장치</b><small>처리 목록과 별도의 보조 설정</small></span>
        <span className={status?.availability === 'UNAVAILABLE' ? 'text-amber-700' : status?.availability !== 'AVAILABLE' ? 'text-slate-500' : status.kill_switch_active ? 'text-red-700' : 'text-emerald-700'}>{status?.availability === 'UNAVAILABLE' ? '확인할 수 없음' : status?.availability !== 'AVAILABLE' ? '확인 중' : status.kill_switch_active ? '전체 중지' : '정상'}</span>
      </summary>
      <div className="border-t border-slate-200 p-4">
        <p className="ops-readable text-sm leading-6 text-slate-600">
          비용 급증 시에만 사용하세요. 여기서 자동 작업을 중지해도{' '}
          <span className="whitespace-nowrap">진행 중인 고객 작업 결과는 유지됩니다.</span>
        </p>
        {error ? <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p> : null}
        {notice ? <p role="status" className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">{notice}</p> : null}
        {status?.availability === 'UNAVAILABLE' ? (
          <section className="ops-readable mt-4 rounded-lg border border-amber-300 bg-amber-50 p-4 text-sm leading-6 text-amber-950">
            <h3 className="font-bold">비용 기록을 지금 확인할 수 없습니다</h3>
            <dl className="mt-3 grid gap-3">
              <div><dt className="font-semibold">무슨 문제인지</dt><dd>비용 현황을 불러오는 연결이 잠시 끊겼습니다.</dd></div>
              <div><dt className="font-semibold">고객 영향</dt><dd>자동 작업은 계속될 수 있지만, 현재 사용량과 전체 중지 여부를 정확히 확인할 수 없습니다.</dd></div>
              <div><dt className="font-semibold">지금 할 일</dt><dd>아래 버튼으로 다시 확인하세요. 계속 같은 상태라면 문의용 정보를 복사해 개발팀에 전달하세요.</dd></div>
            </dl>
            <div className="mt-4 flex flex-col gap-2 sm:flex-row">
              <button type="button" disabled={refreshing} onClick={() => void load()} className="ops-control rounded-lg bg-amber-800 px-4 text-sm font-bold text-white disabled:cursor-wait disabled:opacity-60">{refreshing ? '확인 중…' : '상태 다시 확인'}</button>
              <button type="button" onClick={() => void copyDeveloperContext()} className="ops-control rounded-lg border border-amber-400 bg-white px-4 text-sm font-semibold">개발팀 문의용 정보 복사</button>
            </div>
          </section>
        ) : null}
        {status?.availability === 'AVAILABLE' ? (
          <>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 p-3">
              <div><p className="font-semibold text-slate-800">전체 자동 작업</p><p className="mt-0.5 text-xs text-slate-500">콘텐츠·이미지·AI 언급률 측정</p></div>
              <button type="button" disabled={busy === 'kill'} onClick={() => void toggleKillSwitch()} className={`ops-control rounded-lg px-4 text-sm font-bold text-white disabled:opacity-45 ${status.kill_switch_active ? 'bg-emerald-700' : 'bg-red-700'}`}>{busy === 'kill' ? '확인 중…' : status.kill_switch_active ? '중지 해제' : '전체 중지'}</button>
            </div>
            <div className="mt-3 grid gap-3 lg:grid-cols-3">
              {status.categories.map((category) => {
                const percent = ratio(category.daily_used, category.daily_limit)
                const raised = category.daily_limit > category.daily_limit_default
                return (
                  <section key={category.category} className="rounded-lg border border-slate-200 p-3">
                    <div className="flex justify-between gap-2"><h3 className="font-semibold text-slate-800">{category.label}</h3><span className="text-xs tabular-nums text-slate-500">{category.daily_used}/{category.daily_limit || '∞'}</span></div>
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-slate-100"><div className={percent >= 100 ? 'h-full bg-red-600' : percent >= 80 ? 'h-full bg-amber-500' : 'h-full bg-blue-600'} style={{ width: `${percent}%` }} /></div>
                    <p className="mt-2 text-xs leading-5 text-slate-500">오늘 실행 {category.daily_actual ?? '확인 불가'}건 · 월 {category.monthly_used ?? '확인 불가'}/{category.monthly_limit || '∞'}건</p>
                    {canRaiseLimit ? <div className="mt-2 flex gap-2"><button type="button" disabled={busy === category.category} onClick={() => void updateLimit(category, category.daily_limit_default * 2)} className="ops-control flex-1 rounded-lg border border-slate-300 px-2 text-xs font-semibold disabled:opacity-45">오늘 한도 2배</button>{raised ? <button type="button" disabled={busy === category.category} onClick={() => void updateLimit(category, null)} className="ops-control flex-1 rounded-lg border border-slate-300 px-2 text-xs font-semibold disabled:opacity-45">기본 한도로 되돌리기</button> : null}</div> : <p className="mt-2 text-xs text-slate-500">하루 사용 한도 변경은 계정 소유자만 가능합니다.</p>}
                  </section>
                )
              })}
            </div>
          </>
        ) : status === null ? <p className="mt-4 text-sm text-slate-500">비용 상태를 불러오는 중입니다.</p> : null}
      </div>
    </details>
  )
}
