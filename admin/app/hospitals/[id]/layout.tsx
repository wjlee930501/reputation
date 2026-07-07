'use client'

import Link from 'next/link'
import { useParams, usePathname } from 'next/navigation'
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
  { label: '온보딩', path: 'onboarding', hint: '신규 병원 자료 인입 + 운영 기준 승인까지 한 화면에서' },
  { label: '프로파일', path: 'profile', hint: '병원 기본 정보' },
  { label: '콘텐츠', path: 'content', hint: '초안 검수·발행' },
  { label: '스케줄', path: 'schedule', hint: '발행 캘린더' },
  { label: '리포트', path: 'reports', hint: '월간 리포트' },
]

const CONFIG_TABS: Array<{ label: string; path: string; hint: string }> = [
  { label: 'Wiki', path: 'wiki', hint: '검증된 근거 노트 + 사진 공개 토글' },
  { label: '운영 기준', path: 'essence', hint: '콘텐츠 운영 기준(진료 철학·말투·금기 표현) 승인' },
  { label: '환자 질문', path: 'query-targets', hint: 'ChatGPT·Gemini 같은 AI 답변 서비스에 노출시킬 환자 질문 정의' },
  { label: '노출 보완', path: 'exposure-actions', hint: 'AI에 더 잘 노출되도록 보완할 작업과 콘텐츠 가이드 연결' },
]

const ALL_TABS = [...MAIN_TABS, ...CONFIG_TABS]

export default function HospitalLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const pathname = usePathname()
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
    ? STATUS_LABELS[hospital.status] ?? { label: hospital.status, color: 'bg-slate-100 text-slate-700' }
    : null

  const planLabel = hospital?.plan ? PLAN_LABELS[hospital.plan] ?? hospital.plan : null
  const lifecycleAction = getHospitalLifecycleAction(hospital?.status)

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
      <header className="border-b border-slate-200 bg-white px-4 pt-4 pb-0 sm:px-6 lg:px-8 lg:pt-5">
        <div className="mb-4 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between lg:gap-6">
          <div className="min-w-0">
            <Link
              href="/hospitals"
              className="details2 inline-flex items-center gap-1 text-[var(--color-revisit-text-helper)] transition-colors hover:text-[var(--color-revisit-text-title)]"
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
              {lifecycleAction && (
                <button
                  type="button"
                  onClick={() => void handleLifecycleAction()}
                  disabled={lifecycleLoading}
                  className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    lifecycleAction === 'pause'
                      ? 'border-red-200 text-red-700 hover:bg-red-50'
                      : 'border-green-200 text-green-700 hover:bg-green-50'
                  }`}
                >
                  {lifecycleLoading ? '처리 중...' : lifecycleAction === 'pause' ? '일시정지' : '재개'}
                </button>
              )}
            </div>
            {hospital && (
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500 sm:gap-3">
                <span className="font-mono text-slate-400">{hospital.slug}</span>
                {hospital.aeo_domain && (
                  <>
                    <span aria-hidden className="text-slate-300">·</span>
                    <span>
                      병원 정보 허브 도메인 <span className="text-[var(--color-revisit-text-title)]">{hospital.aeo_domain}</span>
                    </span>
                  </>
                )}
                {hospital.site_live && (
                  <span className="inline-flex items-center gap-1 text-emerald-600 font-medium">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                    병원 정보 허브 운영중
                  </span>
                )}
              </div>
            )}
          </div>

          {hospital && (
            <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-500 lg:shrink-0">
              <ProgressDot label="프로파일 완료" done={hospital.profile_complete} />
              <ProgressDot label="초기 진단 리포트 완료" done={hospital.v0_report_done} />
              <ProgressDot label="병원 정보 허브 운영중" done={hospital.site_live} />
              <ProgressDot label="스케줄 설정" done={hospital.schedule_set} />
            </div>
          )}
        </div>

        {/* Tab navigation */}
        <nav className="-mb-px flex items-stretch gap-1 overflow-x-auto pb-px" aria-label="병원 작업 탭">
          {ALL_TABS.map((tab, idx) => {
            const href = `/hospitals/${hospitalId}/${tab.path}`
            const isActive = pathname.startsWith(href)
            const isConfig = idx >= MAIN_TABS.length
            return (
              <span key={tab.path} className="contents">
                {idx === MAIN_TABS.length && (
                  <span
                    aria-hidden
                    className="mx-1 self-center text-xs font-medium text-slate-300 select-none"
                    title="설정"
                  >
                    ⚙
                  </span>
                )}
                <Link
                  href={href}
                  aria-current={isActive ? 'page' : undefined}
                  aria-label={`${tab.label}: ${tab.hint}`}
                  className={`group min-w-[8.5rem] border-b-2 px-3 py-2.5 text-left text-sm font-medium transition-colors sm:min-w-fit sm:px-4 ${
                    isConfig
                      ? isActive
                        ? 'border-purple-500 text-purple-700'
                        : 'border-transparent text-slate-400 hover:text-slate-600 hover:border-slate-200'
                      : isActive
                        ? 'border-blue-600 text-blue-700'
                        : 'border-transparent text-slate-500 hover:text-slate-800 hover:border-slate-200'
                  }`}
                >
                  <span className="block whitespace-nowrap">{tab.label}</span>
                  <span className="mt-0.5 hidden max-w-[18rem] truncate text-[11px] font-normal text-slate-400 sm:block">
                    {tab.hint}
                  </span>
                </Link>
              </span>
            )
          })}
        </nav>
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
      <span className={done ? 'text-[var(--color-revisit-text-title)]' : 'text-[var(--color-revisit-text-caption)]'}>{label}</span>
    </span>
  )
}
