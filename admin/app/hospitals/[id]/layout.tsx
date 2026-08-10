'use client'

import Link from 'next/link'
import { useParams, usePathname, useRouter } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'
import { ApiError, fetchAPI } from '@/lib/api'
import {
  getHospitalLifecycleAction,
  hospitalLifecycleActionPath,
  hospitalLifecycleConfirmMessage,
} from '@/lib/hospital-lifecycle'
import { Hospital, PLAN_LABELS, STATUS_LABELS } from '@/types'
import { HospitalHeaderContext } from './hospital-context'

const MAIN_TABS: Array<{ label: string; path: string; hint: string }> = [
  { label: '대시보드', path: 'dashboard', hint: 'AI 언급률과 운영 준비 상태 한눈에 보기' },
  { label: '온보딩', path: 'onboarding', hint: '병원 자료 입력과 운영 기준 승인' },
  { label: '병원 기본 정보', path: 'profile', hint: '병원과 원장 기본 정보' },
  { label: '콘텐츠', path: 'content', hint: '자동 발행·공개 내용 확인' },
  { label: '스케줄', path: 'schedule', hint: '발행 캘린더' },
  { label: '리포트', path: 'reports', hint: '월간 리포트' },
]

const CONFIG_TABS: Array<{ label: string; path: string; hint: string }> = [
  { label: 'Wiki', path: 'wiki', hint: '검증된 근거 노트 + 사진 공개 토글' },
  { label: '운영 기준', path: 'essence', hint: '콘텐츠 운영 기준(진료 철학·말투·금기 표현) 승인' },
  { label: '환자 질문', path: 'query-targets', hint: 'ChatGPT·Gemini 같은 AI 답변 서비스에 노출시킬 환자 질문 정의' },
  { label: '노출 보완', path: 'exposure-actions', hint: 'AI에 더 잘 노출되도록 보완할 작업과 콘텐츠 가이드 연결' },
]

export default function HospitalLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const hospitalId = params.id
  const [hospital, setHospital] = useState<Hospital | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [lifecycleLoading, setLifecycleLoading] = useState(false)
  const [lifecycleError, setLifecycleError] = useState<string | null>(null)

  const refetch = useCallback(async () => {
    try {
      const data = await fetchAPI<Hospital>(`/admin/hospitals/${hospitalId}`)
      setHospital(data)
      setNotFound(false)
      setLoadError(null)
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 404) {
        setNotFound(true)
        setLoadError(null)
      } else {
        setLoadError(e instanceof Error ? e.message : '병원 정보를 불러오지 못했습니다.')
      }
    }
  }, [hospitalId])

  useEffect(() => {
    void refetch()
  }, [refetch])

  if (notFound) {
    return (
      <div className="flex min-h-full items-center justify-center p-8">
        <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 text-center shadow-sm">
          <p className="text-lg font-bold text-slate-900">병원을 찾을 수 없습니다.</p>
          <p className="mt-2 text-sm text-slate-500">
            삭제되었거나 주소가 잘못된 병원입니다. 병원 목록에서 다시 선택해 주세요.
          </p>
          <Link
            href="/hospitals"
            className="mt-5 inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            ← 병원 목록으로 돌아가기
          </Link>
        </div>
      </div>
    )
  }

  const statusInfo = hospital
    ? STATUS_LABELS[hospital.status] ?? { label: '상태 확인 필요', color: 'bg-slate-100 text-slate-700' }
    : null

  const planLabel = hospital?.plan ? PLAN_LABELS[hospital.plan] ?? '요금제 확인 필요' : null
  const lifecycleAction = getHospitalLifecycleAction(hospital?.status)
  const visibleLifecycleAction = lifecycleAction === 'resume' && !hospital?.schedule_set ? null : lifecycleAction
  const activeConfigTab = CONFIG_TABS.find((tab) => pathname.startsWith(`/hospitals/${hospitalId}/${tab.path}`))
  const activeMainTab = MAIN_TABS.find((tab) => pathname.startsWith(`/hospitals/${hospitalId}/${tab.path}`))
  const activeTab = activeConfigTab ?? activeMainTab ?? MAIN_TABS[0]

  async function handleLifecycleAction() {
    if (!hospital || !lifecycleAction) return
    if (!confirm(hospitalLifecycleConfirmMessage(lifecycleAction))) return
    setLifecycleLoading(true)
    setLifecycleError(null)
    try {
      const updated = await fetchAPI<Hospital>(hospitalLifecycleActionPath(hospitalId, lifecycleAction), {
        method: 'POST',
      })
      setHospital(updated)
    } catch (e: unknown) {
      setLifecycleError(e instanceof Error ? e.message : '병원 상태 변경에 실패했습니다.')
    } finally {
      setLifecycleLoading(false)
    }
  }

  return (
    <HospitalHeaderContext.Provider value={{ hospital, refetch }}>
    <div className="flex min-h-full flex-col">
      {/* Hospital header */}
      <header className="border-b border-slate-200 bg-white px-4 py-3 lg:px-8 lg:pb-0 lg:pt-5">
        <div className="lg:hidden">
          <div className="flex min-h-11 items-center gap-3">
            <Link href="/hospitals" aria-label="병원 목록으로" className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-slate-200 text-slate-500">
              ←
            </Link>
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 items-center gap-2">
                <h1 className="truncate text-base font-bold text-slate-900">{hospital?.name ?? '병원 불러오는 중'}</h1>
                {statusInfo && (
                  <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${statusInfo.color}`}>
                    {statusInfo.label}
                  </span>
                )}
              </div>
              <p className="mt-0.5 truncate text-xs text-slate-500">
                {hospital?.aeo_domain ? `공개 주소 ${hospital.aeo_domain}` : '공개 주소 준비 중'}
              </p>
            </div>
            {hospital && (
              <details className="group relative shrink-0">
                <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-1 rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-700 [&::-webkit-details-marker]:hidden">
                  상태 <span aria-hidden className="transition-transform group-open:rotate-180">⌄</span>
                </summary>
                <div className="absolute right-0 top-[calc(100%+8px)] z-40 w-[min(21rem,calc(100vw-2rem))] rounded-xl border border-slate-200 bg-white p-4 shadow-xl">
                  <p className="text-xs font-semibold text-slate-900">운영 준비 상태</p>
                  <div className="mt-3 grid gap-2 text-xs text-slate-600">
                    <ProgressDot label="필수 병원 정보 완료" done={hospital.profile_complete} />
                    <ProgressDot label="초기 진단 리포트 완료" done={hospital.v0_report_done} />
                    <ProgressDot label="스케줄 설정" done={hospital.schedule_set} />
                    <ProgressDot label="병원 정보 허브 운영 중" done={hospital.site_live} />
                  </div>
                  {planLabel && <p className="mt-3 border-t border-slate-100 pt-3 text-xs text-slate-500">운영량 {planLabel}</p>}
                </div>
              </details>
            )}
          </div>
          <label className="mt-2 block">
            <span className="sr-only">현재 병원 작업 화면</span>
            <select
              value={activeTab.path}
              onChange={(event) => router.push(`/hospitals/${hospitalId}/${event.target.value}`)}
              className="min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-800 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
            >
              <optgroup label="주요 작업">
                {MAIN_TABS.map((tab) => <option key={tab.path} value={tab.path}>{tab.label} — {tab.hint}</option>)}
              </optgroup>
              <optgroup label="운영 설정">
                {CONFIG_TABS.map((tab) => <option key={tab.path} value={tab.path}>{tab.label} — {tab.hint}</option>)}
              </optgroup>
            </select>
          </label>
        </div>

        <div className="mb-4 hidden flex-col gap-4 lg:flex lg:flex-row lg:items-start lg:justify-between lg:gap-6">
          <div className="min-w-0">
            <Link
              href="/hospitals"
              className="details2 inline-flex min-h-11 items-center gap-1 text-[var(--color-revisit-text-helper)] transition-colors hover:text-[var(--color-revisit-text-title)]"
            >
              ← 병원 목록
            </Link>
            <div className="flex items-center gap-3 mt-2 flex-wrap">
              <h1 className="heading3 truncate text-[var(--color-revisit-text-title)]">
                {hospital?.name ?? '불러오는 중...'}
              </h1>
              {statusInfo && (
                <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${statusInfo.color}`}>
                  {statusInfo.label}
                </span>
              )}
              {planLabel && (
                <span className="details2 inline-flex rounded-full bg-[var(--color-revisit-coolgrey-90)] px-2.5 py-0.5 text-[var(--color-revisit-text-helper)]">
                  {planLabel}
                </span>
              )}
              {visibleLifecycleAction && (
                <button
                  type="button"
                  onClick={() => void handleLifecycleAction()}
                  disabled={lifecycleLoading}
                  className={`inline-flex min-h-11 items-center rounded-lg border px-3 py-2 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    visibleLifecycleAction === 'pause'
                      ? 'border-red-200 text-red-700 hover:bg-red-50'
                      : 'border-green-200 text-green-700 hover:bg-green-50'
                  }`}
                >
                  {lifecycleLoading ? '처리 중...' : visibleLifecycleAction === 'pause' ? '일시정지' : '재개'}
                </button>
              )}
            </div>
            {hospital && (
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500 sm:gap-3">
                <span>
                  {hospital.aeo_domain ? (
                    <>공개 주소 <span className="text-[var(--color-revisit-text-title)]">{hospital.aeo_domain}</span></>
                  ) : '공개 주소 준비 중'}
                </span>
                {hospital.site_live && (
                  <span className="inline-flex items-center gap-1 text-emerald-600 font-medium">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    병원 정보 허브 운영 중
                  </span>
                )}
              </div>
            )}
          </div>

          {hospital && (
            <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-500 lg:shrink-0">
              <ProgressDot label="필수 병원 정보 완료" done={hospital.profile_complete} />
              <ProgressDot label="초기 진단 리포트 완료" done={hospital.v0_report_done} />
              <ProgressDot label="스케줄 설정" done={hospital.schedule_set} />
              <ProgressDot label="병원 정보 허브 운영 중" done={hospital.site_live} />
            </div>
          )}
        </div>

        {/* Tab navigation */}
        <div className="-mb-px hidden items-end gap-2 lg:flex">
          <nav className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto pb-px" aria-label="병원 주요 작업">
            {MAIN_TABS.map((tab) => {
              const href = `/hospitals/${hospitalId}/${tab.path}`
              const isActive = pathname.startsWith(href)
              return (
                <Link
                  key={tab.path}
                  href={href}
                  aria-current={isActive ? 'page' : undefined}
                  aria-label={`${tab.label}: ${tab.hint}`}
                  title={tab.hint}
                  className={`inline-flex min-h-11 shrink-0 items-center border-b-2 px-3 text-sm font-medium transition-colors sm:px-4 ${
                    isActive
                      ? 'border-blue-600 text-blue-700'
                      : 'border-transparent text-slate-500 hover:border-slate-200 hover:text-slate-800'
                  }`}
                >
                  {tab.label}
                </Link>
              )
            })}
          </nav>
          <details className="group relative shrink-0 pb-px">
            <summary
              className={`inline-flex min-h-11 cursor-pointer list-none items-center gap-1.5 border-b-2 px-3 text-sm font-medium [&::-webkit-details-marker]:hidden ${
                activeConfigTab
                  ? 'border-purple-500 text-purple-700'
                  : 'border-transparent text-slate-500 hover:border-slate-200 hover:text-slate-800'
              }`}
            >
              {activeConfigTab?.label ?? '운영 설정'}
              <span aria-hidden className="text-[10px] transition-transform group-open:rotate-180">▼</span>
            </summary>
            <nav
              aria-label="병원 운영 설정"
              className="absolute right-0 top-full z-30 mt-2 w-[min(20rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-slate-200 bg-white p-1.5 shadow-xl"
            >
              {CONFIG_TABS.map((tab) => {
                const href = `/hospitals/${hospitalId}/${tab.path}`
                const isActive = pathname.startsWith(href)
                return (
                  <Link
                    key={tab.path}
                    href={href}
                    aria-current={isActive ? 'page' : undefined}
                    className={`block rounded-lg px-3 py-2.5 ${isActive ? 'bg-purple-50 text-purple-800' : 'text-slate-700 hover:bg-slate-50'}`}
                  >
                    <span className="block text-sm font-semibold">{tab.label}</span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-slate-500">{tab.hint}</span>
                  </Link>
                )
              })}
            </nav>
          </details>
        </div>
      </header>

      {loadError && (
        <div className="mx-4 mt-4 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 sm:mx-6 lg:mx-8">
          <span>병원 정보를 불러오지 못했습니다. ({loadError})</span>
          <button
            type="button"
            onClick={() => void refetch()}
            className="shrink-0 rounded-md border border-amber-300 bg-white px-3 py-1 text-xs font-medium text-amber-800 hover:bg-amber-100"
          >
            다시 시도
          </button>
        </div>
      )}

      {lifecycleError && (
        <div className="mx-4 mt-4 flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-4 py-2.5 text-sm text-red-700 sm:mx-6 lg:mx-8">
          <span>{lifecycleError}</span>
          <button
            type="button"
            onClick={() => setLifecycleError(null)}
            className="shrink-0 rounded-md border border-red-300 bg-white px-3 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
          >
            닫기
          </button>
        </div>
      )}

      {/* Page content */}
      <div className="min-w-0 flex-1 overflow-auto">
        {children}
      </div>
    </div>
    </HospitalHeaderContext.Provider>
  )
}

function ProgressDot({ label, done }: { label: string; done: boolean | undefined }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={`h-2 w-2 rounded-full ${done ? 'bg-[var(--color-revisit-green-50)]' : 'bg-[var(--color-revisit-coolgrey-70)]'}`}
        aria-hidden
      />
      <span className={done ? 'text-[var(--color-revisit-text-title)]' : 'text-[var(--color-revisit-text-caption)]'}>
        {label} · {done ? '완료' : '대기'}
      </span>
    </span>
  )
}
