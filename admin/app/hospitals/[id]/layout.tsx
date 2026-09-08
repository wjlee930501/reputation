'use client'

import Link from 'next/link'
import { useParams, usePathname, useRouter } from 'next/navigation'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, fetchAPI } from '@/lib/api'
import {
  getHospitalLifecycleAction,
  hospitalLifecycleActionPath,
  hospitalLifecycleConfirmMessage,
} from '@/lib/hospital-lifecycle'
import { readHospitalDomainStatus } from '@/lib/hospital-domain-status'
import {
  describeContentState,
  describeDomainState,
  describePublicService,
  humanRemaining,
  stateTone,
  type RemainingCondition,
  type StateKind,
  type StateTone,
} from '@/lib/hospital-states'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { Hospital, HospitalOverview, PLAN_CONTRACT_LABELS } from '@/types'
import { HospitalHeaderContext } from './hospital-context'

const MAIN_TABS: Array<{ label: string; path: string; hint: string }> = [
  { label: '현황', path: '', hint: '3상태 · 예외 · 이번 달 요약' },
  { label: '운영 요약', path: 'dashboard', hint: 'AI 답변에서 병원이 언급되는 정도와 운영 준비 상태를 한눈에 봅니다.' },
  { label: '온보딩', path: 'onboarding', hint: '병원 자료를 입력하고 콘텐츠 운영 기준을 준비합니다.' },
  { label: '병원 정보', path: 'info', hint: '병원·원장·진료·연락처·공식 채널과 근거 자료' },
  { label: '병원 기본 정보', path: 'profile', hint: '병원과 원장 기본 정보' },
  { label: '콘텐츠', path: 'content', hint: '자동 발행·공개 내용 확인' },
  { label: '발행 일정', path: 'schedule', hint: '월 발행 편수와 발행 요일' },
  { label: '보고서', path: 'reports', hint: '월간 보고서' },
]

// 현황은 병원 화면의 첫 주소(`/hospitals/{id}`)라 경로 조각이 없다. 접두 비교만 쓰면
// 어떤 하위 화면에서도 현황이 함께 켜지므로, 빈 경로만 정확히 일치로 판정한다.
function tabHref(hospitalId: string, path: string): string {
  return path ? `/hospitals/${hospitalId}/${path}` : `/hospitals/${hospitalId}`
}

function isTabActive(pathname: string, hospitalId: string, path: string): boolean {
  const href = tabHref(hospitalId, path)
  return path ? pathname.startsWith(href) : pathname === href
}

const CONFIG_TABS: Array<{ label: string; path: string; hint: string }> = [
  { label: '자료 모음', path: 'wiki', hint: '검증된 근거 노트와 사진 권리·공개 상태' },
  { label: '운영 기준', path: 'essence', hint: '콘텐츠 운영 기준 준비와 예외 확인' },
  { label: '환자 질문', path: 'query-targets', hint: 'ChatGPT·Gemini에 확인할 환자 질문 정의' },
  { label: '노출 보완', path: 'exposure-actions', hint: 'AI 답변에서 병원이 덜 언급되는 이유와 보완 작업' },
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
  const [overview, setOverview] = useState<HospitalOverview | null>(null)
  // 상태를 못 받아온 것과 "상태가 없다"는 다르다 — 조용히 비우면 헤더가 아무 말도 하지 않는다.
  const [overviewFailed, setOverviewFailed] = useState(false)
  // 컨텍스트를 초기 렌더에 쓰는 하위 페이지(profile/onboarding)가 "아직 못 받아옴"과
  // "받아왔는데 실패/없음"을 구분할 수 있게 — 첫 refetch가 끝나면 false로 고정된다.
  const [headerLoading, setHeaderLoading] = useState(true)
  const [notFound, setNotFound] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loadErrorStatus, setLoadErrorStatus] = useState<number | null>(null)
  const [lifecycleLoading, setLifecycleLoading] = useState(false)
  const [lifecycleError, setLifecycleError] = useState<string | null>(null)

  const requestSeq = useRef(0)

  const refetch = useCallback(async () => {
    // 일시정지·재개 직후 다시 받는 중에 먼저 나간 요청이 늦게 도착하면 방금 바뀐 상태를
    // 옛 응답으로 덮는다 — 가장 마지막 요청의 답만 화면에 쓴다.
    const request = ++requestSeq.current
    // 3상태(overview)만 못 받아도 헤더는 이름으로 계속 뜬다 — 한쪽 실패가
    // 다른 쪽을 지우지 않게 두 호출을 따로 받는다.
    const [detail, states] = await Promise.allSettled([
      fetchAPI<Hospital>(`/admin/hospitals/${hospitalId}`),
      fetchAPI<HospitalOverview>(`/admin/hospitals/${hospitalId}/overview`),
    ])
    if (request !== requestSeq.current) return
    setOverview(states.status === 'fulfilled' ? states.value : null)
    setOverviewFailed(states.status === 'rejected')
    if (detail.status === 'fulfilled') {
      setHospital(detail.value)
      setNotFound(false)
      setLoadError(null)
      setLoadErrorStatus(null)
    } else {
      const e: unknown = detail.reason
      if (e instanceof ApiError && e.status === 404) {
        setNotFound(true)
        setLoadError(null)
        setLoadErrorStatus(null)
      } else {
        setLoadErrorStatus(e instanceof ApiError ? e.status : null)
        setLoadError(
          e instanceof ApiError && e.status === 429
            ? '요청이 잠시 제한되었습니다. 잠시 후 병원 정보를 다시 불러와 주세요.'
            : e instanceof Error
              ? e.message
              : '병원 정보를 불러오지 못했습니다.',
        )
      }
    }
    setHeaderLoading(false)
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

  const planLabel = hospital?.plan ? PLAN_CONTRACT_LABELS[hospital.plan] ?? '요금제 확인 필요' : null
  // 3상태는 서버 판정(overview)만 읽는다 — 헤더가 따로 판정하면 목록·현황과 다른 답을 낸다.
  // 공개 서비스 상태가 곧 이 병원의 상태다. 옛 status 배지를 함께 두면 ACTIVE인데 공개
  // 페이지가 없는 병원이 '운영 중'으로 보인다.
  const publicCard = overview ? describePublicService(overview.public_service) : null
  const contentCard = overview ? describeContentState(overview.content) : null
  const domainCard = overview ? describeDomainState(overview.domain) : null
  // 주소는 주소만 말한다 — 마지막 확인 시각은 자기 도메인 상태가 한 번만 말한다.
  const domainStatus = hospital ? readHospitalDomainStatus(hospital) : null
  const publicAddress = domainStatus ? domainStatus.url ?? domainStatus.detail : null
  // 재개 가능 여부는 서버 게이트(활성화 조건·자기 도메인 DNS)가 결정한다 — 발행 일정은 조건이 아니다(H-07).
  const lifecycleAction = getHospitalLifecycleAction(hospital?.status)
  const activeConfigTab = CONFIG_TABS.find((tab) => isTabActive(pathname, hospitalId, tab.path))
  const activeMainTab = MAIN_TABS.find((tab) => isTabActive(pathname, hospitalId, tab.path))
  const activeTab = activeConfigTab ?? activeMainTab ?? MAIN_TABS[0]

  async function handleLifecycleAction() {
    if (!hospital || !lifecycleAction) return
    if (!confirm(hospitalLifecycleConfirmMessage(lifecycleAction))) return
    setLifecycleLoading(true)
    setLifecycleError(null)
    try {
      await fetchAPI<Hospital>(hospitalLifecycleActionPath(hospitalId, lifecycleAction), {
        method: 'POST',
      })
      // 일시정지·재개는 공개 서비스 상태를 바꾼다 — 헤더의 3상태까지 같이 다시 받는다.
      await refetch()
    } catch (e: unknown) {
      setLifecycleError(e instanceof Error ? e.message : '병원 상태 변경에 실패했습니다.')
    } finally {
      setLifecycleLoading(false)
    }
  }

  return (
    <HospitalHeaderContext.Provider value={{ hospital, overview, loading: headerLoading, refetch }}>
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
                <h1 className="truncate text-base font-bold text-slate-900">
                  {hospital?.name ?? (loadError ? '병원 정보 확인 필요' : '병원 불러오는 중')}
                </h1>
              </div>
              {/* 자기 도메인이 없다고 "준비 중"은 아니다 — 기본 플랫폼 주소로 이미
                  서비스 중인 병원이 대부분이고, 그걸 준비 중이라 하면 운영자가 살아
                  있는 주소를 없는 것으로 안다(O-7). 목록·패널과 같은 판정을 쓴다. */}
              <p className="mt-0.5 truncate text-xs text-slate-500">
                {publicAddress
                  ? `공개 주소 ${publicAddress}`
                  : loadError
                    ? '병원 정보를 다시 불러와 주세요'
                    : '공개 주소 확인 중'}
              </p>
            </div>
            {(overview || overviewFailed) && (
              <details className="group relative shrink-0">
                <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-1 rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-700 [&::-webkit-details-marker]:hidden">
                  상태 <span aria-hidden className="transition-transform group-open:rotate-180">⌄</span>
                </summary>
                <div className="absolute right-0 top-[calc(100%+8px)] z-40 w-[min(21rem,calc(100vw-2rem))] rounded-xl border border-slate-200 bg-white p-4 shadow-xl">
                  {overview ? (
                    <div className="grid gap-3">
                      <StateChip title="공개 서비스" kind={overview.public_service.kind} description={publicCard} actions={humanRemaining(overview.public_service.remaining)} />
                      <StateChip title="콘텐츠 준비" kind={overview.content.kind} description={contentCard} actions={humanRemaining(overview.content.remaining)} />
                      {domainCard && <StateChip title="자기 도메인" kind={overview.domain.kind} description={domainCard} />}
                    </div>
                  ) : (
                    <OverviewFailureChip onRetry={() => void refetch()} />
                  )}
                  {planLabel && <p className="mt-3 border-t border-slate-100 pt-3 text-xs text-slate-500">{ADMIN_COPY.plan} {planLabel}</p>}
                </div>
              </details>
            )}
          </div>
          <label className="mt-2 block">
            <span className="sr-only">현재 병원 작업 화면</span>
            <select
              value={activeTab.path}
              onChange={(event) => router.push(tabHref(hospitalId, event.target.value))}
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

        <div className="mb-4 hidden flex-col gap-4 lg:flex lg:flex-row lg:items-start lg:justify-between lg:gap-4 xl:gap-6">
          <div className="min-w-0">
            <Link
              href="/hospitals"
              className="details2 inline-flex min-h-11 items-center gap-1 text-[var(--color-revisit-text-helper)] transition-colors hover:text-[var(--color-revisit-text-title)]"
            >
              ← 병원 목록
            </Link>
            <div className="flex items-center gap-3 mt-2 flex-wrap">
              <h1 className="heading3 truncate text-[var(--color-revisit-text-title)]">
                {hospital?.name ?? (loadError ? '병원 정보 확인 필요' : '불러오는 중...')}
              </h1>
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
                  className={`inline-flex min-h-11 items-center rounded-lg border px-3 py-2 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    lifecycleAction === 'pause'
                      ? 'border-red-200 text-red-700 hover:bg-red-50'
                      : 'border-green-200 text-green-700 hover:bg-green-50'
                  }`}
                >
                  {lifecycleLoading ? '처리 중...' : lifecycleAction === 'pause' ? '일시정지' : '재개'}
                </button>
              )}
            </div>
            {publicAddress && (
              <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500 sm:gap-3">
                <span className="inline-flex min-w-0 max-w-[280px] items-baseline gap-1">
                  공개 주소{' '}
                  <span className="truncate text-[var(--color-revisit-text-title)]" title={publicAddress}>
                    {publicAddress}
                  </span>
                </span>
                {/* 공개 중인지 아닌지는 공개 서비스 상태가 말한다 — 이 줄은 자기 도메인
                    사실만 덧붙인다. 자기 도메인이 없으면 아무 말도 하지 않는다. */}
                {domainCard && overview && (
                  <>
                    <span className={`inline-flex items-center gap-1 font-medium ${domainTextClass(stateTone(overview.domain.kind))}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${domainDotClass(stateTone(overview.domain.kind))}`} />
                      {domainCard.label}
                    </span>
                    {domainCard.detail && <span className="text-slate-400">{domainCard.detail}</span>}
                  </>
                )}
              </div>
            )}
          </div>

          {overview && (
            <div className="hidden max-w-xl flex-wrap items-start gap-x-6 gap-y-3 lg:flex lg:shrink-0">
              <StateChip title="공개 서비스" kind={overview.public_service.kind} description={publicCard} actions={humanRemaining(overview.public_service.remaining)} />
              <StateChip title="콘텐츠 준비" kind={overview.content.kind} description={contentCard} actions={humanRemaining(overview.content.remaining)} />
              {domainCard && <StateChip title="자기 도메인" kind={overview.domain.kind} description={domainCard} />}
            </div>
          )}
          {overviewFailed && (
            <div className="hidden lg:flex lg:shrink-0">
              <OverviewFailureChip onRetry={() => void refetch()} />
            </div>
          )}
        </div>

        {/* Tab navigation */}
        <div className="-mb-px hidden items-end gap-2 lg:flex">
          <nav className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto pb-px" aria-label="병원 주요 작업">
            {MAIN_TABS.map((tab) => {
              const href = tabHref(hospitalId, tab.path)
              const isActive = isTabActive(pathname, hospitalId, tab.path)
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
          <span>
            {loadErrorStatus === 429 ? loadError : `병원 정보를 불러오지 못했습니다. (${loadError})`}
          </span>
          <button
            type="button"
            onClick={() => void refetch()}
            className="min-h-11 shrink-0 rounded-md border border-amber-300 bg-white px-3 py-2 text-xs font-medium text-amber-800 hover:bg-amber-100"
          >
            병원 정보 다시 불러오기
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

function stateChipClass(tone: StateTone): string {
  switch (tone) {
    case 'good':
      return 'bg-emerald-50 text-emerald-700 border border-emerald-200'
    case 'warn':
      return 'bg-amber-50 text-amber-800 border border-amber-200'
    case 'paused':
      return 'bg-slate-100 text-slate-600 border border-slate-200'
    case 'neutral':
      return 'bg-sky-50 text-sky-700 border border-sky-200'
  }
}

function domainTextClass(tone: StateTone): string {
  return tone === 'good' ? 'text-emerald-600' : tone === 'warn' ? 'text-amber-600' : 'text-slate-500'
}

function domainDotClass(tone: StateTone): string {
  return tone === 'good' ? 'bg-emerald-500' : tone === 'warn' ? 'bg-amber-500' : 'bg-slate-400'
}

/**
 * 상태를 못 받아왔을 때. 옛 status 배지로 "운영 중"을 지어내지 않고, 못 받아왔다는
 * 사실과 다시 받는 버튼만 보인다 — 아무 말도 안 하면 화면이 비어 보인다.
 */
function OverviewFailureChip({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] text-slate-400">공개 서비스</p>
      <span className="mt-0.5 inline-flex rounded-full border border-amber-200 bg-amber-50 px-2.5 py-0.5 text-xs font-medium text-amber-800">
        상태 불러오기 실패
      </span>
      <button
        type="button"
        onClick={onRetry}
        className="mt-1 block text-[11px] font-medium text-blue-600 hover:text-blue-800 hover:underline"
      >
        다시 시도
      </button>
    </div>
  )
}

/**
 * 상태 하나 — 라벨과 남은 조건. 링크는 사람이 손댈 조건에만 붙는다(`actions`).
 * 시스템이 처리 중인 조건은 문구로만 말한다: 운영자가 누를 곳이 없기 때문이다.
 */
function StateChip({
  title,
  kind,
  description,
  actions,
}: {
  title: string
  kind: StateKind
  description: { label: string; detail: string | null } | null
  actions?: RemainingCondition[]
}) {
  if (!description) return null
  return (
    <div className="min-w-0">
      <p className="text-[11px] text-slate-400">{title}</p>
      <span className={`mt-0.5 inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${stateChipClass(stateTone(kind))}`}>
        {description.label}
      </span>
      {description.detail && (
        <p className="mt-1 max-w-[18rem] text-[11px] leading-relaxed text-slate-500">{description.detail}</p>
      )}
      {(actions ?? [])
        .filter((condition) => condition.href)
        .map((condition) => (
          <Link
            key={condition.key}
            href={condition.href as string}
            className="mt-1 block text-[11px] font-medium text-blue-600 hover:text-blue-800 hover:underline"
          >
            {condition.label} →
          </Link>
        ))}
    </div>
  )
}
