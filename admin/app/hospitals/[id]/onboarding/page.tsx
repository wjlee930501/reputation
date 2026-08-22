'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, fetchAPI } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { safeOperatorError } from '@/lib/operations-journey'
import {
  addProfileUrlCandidate,
  isProfileOnlyCandidate,
  type ProfileUrlCandidate,
} from '@/lib/onboarding-candidate'
import {
  buildClinicVisualChecklist,
  type ClinicVisualItem,
} from '@/lib/clinic-visual-readiness'
import type { Handoff, MeasurementRun } from '@/types'
import {
  deriveHandoffDueStatus,
  deriveOnboardingSteps,
  deriveOnboardingSummary,
  type LifecycleReadiness,
  type OnboardingStep as StepDef,
} from '@/lib/onboarding-lifecycle'
import NaverBlogBulkForm from './NaverBlogBulkForm'

interface Hospital {
  id: string
  name: string
  slug: string
  profile_complete: boolean
  v0_report_done: boolean
  schedule_set: boolean
  site_built?: boolean
  site_live?: boolean
  status: string
  aeo_domain?: string | null
  website_url?: string | null
  blog_url?: string | null
  kakao_channel_url?: string | null
  google_business_profile_url?: string | null
  google_maps_url?: string | null
  naver_place_url?: string | null
  logo_url?: string | null
  brand_primary_color?: string | null
  hero_headline?: string | null
  hero_description?: string | null
  site_access_mode?: string | null
}

interface Source {
  id: string
  source_type: string
  title: string
  status: string
  url: string | null
  file_url: string | null
  file_access_url: string | null
  mime_type: string | null
  file_size_bytes: number | null
  is_public: boolean
  raw_text: string | null
  process_error: string | null
  evidence_note_count: number
  source_metadata: Record<string, unknown>
  display: { source_type_label: string; status_label: string } | null
  created_at: string | null
}

interface Philosophy {
  id: string
  version: number
  status: string
  positioning_statement: string | null
}

const SOURCE_TYPE_OPTIONS: Array<{ value: string; label: string; group: 'TEXT' | 'PHOTO' }> = [
  { value: 'HOMEPAGE', label: '병원 홈페이지', group: 'TEXT' },
  { value: 'NAVER_BLOG', label: '네이버 블로그', group: 'TEXT' },
  { value: 'YOUTUBE', label: '유튜브', group: 'TEXT' },
  { value: 'INTERVIEW', label: '원장 인터뷰지', group: 'TEXT' },
  { value: 'BROCHURE', label: '브로슈어', group: 'TEXT' },
  { value: 'LANDING_PAGE', label: '랜딩 페이지', group: 'TEXT' },
  { value: 'INTERNAL_NOTE', label: '내부 메모', group: 'TEXT' },
  { value: 'OTHER', label: '기타', group: 'TEXT' },
  { value: 'PHOTO_DOCTOR', label: '사진 — 원장', group: 'PHOTO' },
  { value: 'PHOTO_CLINIC_EXTERIOR', label: '사진 — 외관', group: 'PHOTO' },
  { value: 'PHOTO_CLINIC_INTERIOR', label: '사진 — 내부', group: 'PHOTO' },
  { value: 'PHOTO_TREATMENT_ROOM', label: '사진 — 진료/시술실', group: 'PHOTO' },
]

const PHOTO_SOURCE_TYPES = new Set([
  'PHOTO_DOCTOR',
  'PHOTO_CLINIC_EXTERIOR',
  'PHOTO_CLINIC_INTERIOR',
  'PHOTO_TREATMENT_ROOM',
])

function isPhotoSourceType(sourceType: string): boolean {
  return PHOTO_SOURCE_TYPES.has(sourceType)
}

const DOCTOR_ASSET_KIND_OPTIONS = [
  { value: 'VERIFIED_REAL_PERSON', label: '실제 원장 사진 — 본인 확인 완료' },
  { value: 'EDITORIAL_GRAPHIC', label: '캐릭터·일러스트 — 의료진 영역 사용 안 함' },
]

const FACILITY_ASSET_KIND_OPTIONS = [
  { value: 'VERIFIED_FACILITY', label: '실제 병원 공간 사진 — 장소 확인 완료' },
  { value: 'EDITORIAL_GRAPHIC', label: '생성·일러스트 이미지 — 콘텐츠 전용' },
]

function photoAssetKindOptions(sourceType: string) {
  return sourceType === 'PHOTO_DOCTOR'
    ? DOCTOR_ASSET_KIND_OPTIONS
    : FACILITY_ASSET_KIND_OPTIONS
}

function hasProcessableText(source: Source): boolean {
  return (source.raw_text?.trim() ?? '').length > 0
}

function sourceTypeLabel(source: Source): string {
  return source.display?.source_type_label
    ?? SOURCE_TYPE_OPTIONS.find((item) => item.value === source.source_type)?.label
    ?? '자료 유형 확인 필요'
}

function sourceStatusLabel(source: Source): string {
  return source.display?.status_label ?? '처리 상태 확인 필요'
}

function sourceFileFormatLabel(mimeType: string | null): string {
  if (mimeType === 'application/pdf') return 'PDF 문서'
  if (mimeType?.includes('word')) return '문서 파일'
  if (mimeType?.startsWith('image/')) return '이미지 파일'
  return '파일 형식 확인 필요'
}

function measurementPlannedCount(run: MeasurementRun | null): number {
  const platforms = run?.error_summary?.platforms
  if (!platforms || typeof platforms !== 'object' || Array.isArray(platforms)) {
    return run?.query_count ?? 0
  }
  const planned = Object.values(platforms).reduce((sum: number, value) => {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return sum
    const count = (value as Record<string, unknown>).planned_count
    return sum + (typeof count === 'number' ? count : 0)
  }, 0)
  return planned > 0 ? planned : (run?.query_count ?? 0)
}

function getProcessingBlockReason(source: Source): string | null {
  if (source.status === 'EXCLUDED' || source.status === 'PROCESSED') return null
  if (hasProcessableText(source)) return null

  if (source.source_type.startsWith('PHOTO_')) {
    return '사진 자료는 공개 자산으로만 사용되며 본문 근거 추출 대상이 아닙니다.'
  }
  if (source.url) {
    return '이 자료는 본문이 없어 근거 추출할 수 없습니다. 자동 크롤을 다시 시도하거나 본문이 있는 문서/메모를 추가해 주세요.'
  }
  if (source.file_url || source.file_access_url) {
    return '업로드 파일에서 추출된 본문이 없습니다. 텍스트가 포함된 PDF/DOCX인지 확인하거나 인터뷰 메모를 추가해 주세요.'
  }
  return '본문이 없어 근거 추출할 수 없습니다. 본문 직접 입력 자료를 추가해 주세요.'
}

function normalizeUrl(value: string | null | undefined): string | null {
  const cleaned = value?.trim()
  if (!cleaned) return null
  return cleaned.replace(/\/+$/, '').toLowerCase()
}

function urlHost(value: string | null | undefined): string | null {
  const cleaned = value?.trim()
  if (!cleaned) return null
  try {
    const href = cleaned.includes('://') ? cleaned : `https://${cleaned}`
    return new URL(href).hostname.replace(/^www\./, '').toLowerCase()
  } catch {
    return null
  }
}

function getProfileUrlCandidates(hospital: Hospital | null, sources: Source[]): ProfileUrlCandidate[] {
  if (!hospital) return []
  const existingUrls = new Set(
    sources
      .map((source) => normalizeUrl(source.url))
      .filter((url): url is string => Boolean(url)),
  )
  const candidates: ProfileUrlCandidate[] = [
    {
      key: 'website_url',
      title: '병원 공식 홈페이지',
      sourceType: 'HOMEPAGE',
      url: hospital.website_url ?? '',
    },
    {
      key: 'blog_url',
      title: '공식 블로그',
      sourceType: 'NAVER_BLOG',
      url: hospital.blog_url ?? '',
    },
    {
      key: 'naver_place_url',
      title: '네이버 플레이스',
      sourceType: 'OTHER',
      url: hospital.naver_place_url ?? '',
    },
    {
      key: 'google_business_profile_url',
      title: '구글 비즈니스 프로필',
      sourceType: 'OTHER',
      url: hospital.google_business_profile_url ?? '',
    },
    {
      key: 'google_maps_url',
      title: '구글 지도',
      sourceType: 'OTHER',
      url: hospital.google_maps_url ?? '',
    },
    {
      key: 'kakao_channel_url',
      title: '카카오 채널',
      sourceType: 'OTHER',
      url: hospital.kakao_channel_url ?? '',
    },
  ]

  const homepageHosts = new Map<string, number>()
  for (const source of sources) {
    if (source.source_type !== 'HOMEPAGE') continue
    const host = urlHost(source.url)
    if (!host) continue
    homepageHosts.set(host, (homepageHosts.get(host) ?? 0) + 1)
  }
  return candidates.flatMap((candidate) => {
    const normalized = normalizeUrl(candidate.url)
    if (normalized === null) return []
    if (isProfileOnlyCandidate(candidate.key)) return [candidate]
    if (existingUrls.has(normalized)) return []
    const host = urlHost(candidate.url)
    const registeredCount = candidate.sourceType === 'HOMEPAGE' && host
      ? homepageHosts.get(host) ?? 0
      : 0
    return [{ ...candidate, registeredCount }]
  })
}

export default function OnboardingPage() {
  const { id } = useParams<{ id: string }>()
  const [hospital, setHospital] = useState<Hospital | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [philosophies, setPhilosophies] = useState<Philosophy[]>([])
  const [readiness, setReadiness] = useState<LifecycleReadiness | null>(null)
  const [handoff, setHandoff] = useState<Handoff | null>(null)
  const [measurementRuns, setMeasurementRuns] = useState<MeasurementRun[]>([])
  const [checkedAt, setCheckedAt] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [h, s, p, r, handoffs, runs] = await Promise.all([
        fetchAPI(`/admin/hospitals/${id}`),
        fetchAPI(`/admin/hospitals/${id}/essence/sources`),
        fetchAPI(`/admin/hospitals/${id}/essence/philosophies`),
        fetchAPI<LifecycleReadiness>(`/admin/hospitals/${id}/readiness`),
        fetchAPI<Handoff[]>('/admin/handoffs'),
        fetchAPI<MeasurementRun[]>(`/admin/hospitals/${id}/sov/measurement-runs`),
      ])
      setHospital(h as Hospital)
      setSources(Array.isArray(s) ? (s as Source[]) : [])
      setPhilosophies(Array.isArray(p) ? (p as Philosophy[]) : [])
      setReadiness(r)
      setHandoff(handoffs.find((item) => item.hospital_id === id) ?? null)
      setMeasurementRuns(Array.isArray(runs) ? runs : [])
      setCheckedAt(Date.now())
    } catch (e: unknown) {
      setError(safeOperatorError('onboarding', '온보딩 정보 다시 불러오기를 누르세요.'))
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void refresh()
  }, [refresh])

  if (loading && !hospital) {
    return <p>온보딩 정보를 불러오는 중…</p>
  }

  if (!hospital) {
    return (
      <OperatorIssuePanel
        message={error ?? safeOperatorError('onboarding', '온보딩 정보 다시 불러오기를 누르세요.')}
        surface="onboarding"
        onRetry={() => void refresh()}
        retryLabel="온보딩 정보 다시 불러오기"
      />
    )
  }

  const steps = deriveOnboardingSteps(hospital, sources, philosophies, readiness, id, handoff)
  const summary = deriveOnboardingSummary(steps, readiness)
  const latestMeasurementRun = measurementRuns.find((run) => run.run_label === 'V0 first measurement') ?? null
  const latestV0Message = typeof latestMeasurementRun?.error_summary?.safe_error_message === 'string'
    ? latestMeasurementRun.error_summary.safe_error_message
    : null
  const v0IsCurrent = steps.some((step) => step.key === 'v0' && step.status === 'current')
  const blockedReason = v0IsCurrent && latestMeasurementRun?.status === 'FAILED'
    ? `${latestV0Message ?? '외부 AI 측정을 완료하지 못했습니다.'} 성공 ${latestMeasurementRun.success_count}건·실패 ${latestMeasurementRun.failure_count}건·예정 ${measurementPlannedCount(latestMeasurementRun)}건 중 나머지는 추가 비용을 막기 위해 중단했으며, 사람 확인이 필요합니다.`
    : summary.blockedReason
  const onboardingSteps = steps.filter((step) => step.phase === 'onboarding')
  const outcomeSteps = steps.filter((step) => step.phase === 'post_onboarding')
  const completedCount = onboardingSteps.filter((step) => step.status === 'completed').length
  const slaDueAt = handoff?.sla_due_at ? new Date(handoff.sla_due_at) : null
  const handoffDueStatus = deriveHandoffDueStatus(handoff, checkedAt)

  return (
    <div className="min-h-full space-y-6 bg-slate-50 p-4 pt-3 [&_a.inline-flex]:min-h-11 [&_button]:min-h-11 [&_input]:min-h-11 [&_select]:min-h-11 sm:p-6 lg:p-8">
      <header className="rounded-2xl bg-slate-900 p-4 text-white sm:p-7">
        <p className="hidden text-xs font-semibold text-blue-200 sm:block">신규 병원 온보딩</p>
        <h2 className="hidden text-xl font-bold sm:mt-2 sm:block sm:text-2xl">{hospital?.name ?? '온보딩'}</h2>
        <p className="mt-2 hidden max-w-2xl text-sm leading-6 text-blue-50/90 sm:block">
          계약 인수부터 스케줄, 도메인과 공개 운영 시작까지 실제 운영 순서로 확인합니다.
        </p>
        <div data-current-task className="mt-3 rounded-xl border border-white/15 bg-white/10 p-3 sm:mt-5 sm:p-4">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <p className="text-xs font-semibold text-blue-100">지금 해야 할 일</p>
              <p className="mt-1 text-base font-bold text-white sm:text-lg">{summary.headline}</p>
              <p className="mt-1 hidden max-w-3xl text-sm leading-6 text-blue-50/90 sm:block">{summary.detail}</p>
              {blockedReason && (
                <p className="mt-1 break-keep text-sm font-semibold text-red-100">진행이 멈춘 이유: {blockedReason}</p>
              )}
              <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-blue-50/90">
                <div className="flex gap-1"><dt>담당</dt><dd className="font-semibold text-white">{handoff?.ae_owner_name ?? '미지정'}</dd></div>
                {handoff?.state !== 'HANDOFF_ACCEPTED' && (
                  <div className="flex gap-1"><dt>인수 처리 기한</dt><dd className="font-semibold text-white">{slaDueAt && Number.isFinite(slaDueAt.valueOf()) ? slaDueAt.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '미설정'}</dd></div>
                )}
                <div className="flex gap-1"><dt>처리 상태</dt><dd className="font-semibold text-white">{handoffDueStatus.label}</dd></div>
              </dl>
              {handoffDueStatus.isOverdue && (
                <p className="mt-2 break-keep text-sm leading-6 text-amber-100">담당 AE가 아래 다음 작업 버튼을 눌러 계약 정보를 확인하고 인수를 승인해 주세요. 승인이 계속 안 되면 병원명과 현재 화면의 문구를 개발팀에 전달해 주세요.</p>
              )}
            </div>
            {summary.nextActionHref && (
              <a
                href={summary.nextActionHref}
                data-primary-next-action
                className="inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg bg-white px-4 py-2 text-sm font-semibold text-slate-900 hover:bg-blue-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-200 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-900"
              >
                다음 작업: {summary.nextActionLabel}
              </a>
            )}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white">
            {(() => {
              const current = onboardingSteps.find((step) => step.status === 'current')
              return current
                ? `완료 ${completedCount}/${onboardingSteps.length} · 다음 필수: ${current.index + 1}단계 ${current.title}`
                : `완료 ${completedCount}/${onboardingSteps.length}`
            })()}
          </span>
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${summary.stateClassName}`}>
            {summary.stateLabel}
          </span>
          <button
            type="button"
            onClick={refresh}
            className="min-h-11 text-xs font-medium text-blue-100 underline underline-offset-2 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-200"
          >
            새로 고침
          </button>
        </div>
      </header>

      {error && (
        <OperatorIssuePanel message={error} surface="onboarding" onRetry={() => void refresh()} retryLabel="온보딩 정보 다시 불러오기" />
      )}

      <section className="grid gap-4 lg:grid-cols-[260px_1fr]">
        {/* Sidebar progress */}
        <aside className="min-w-0 max-w-full self-start rounded-2xl border border-slate-200 bg-white p-4 lg:sticky lg:top-6">
          <details className="lg:hidden">
            <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 text-sm font-semibold text-slate-800 [&::-webkit-details-marker]:hidden">
              전체 단계 보기
              <span className="text-xs text-slate-500">{completedCount}/{onboardingSteps.length} 완료</span>
            </summary>
            <ol className="mt-2 space-y-1 border-t border-slate-100 pt-2">
              {onboardingSteps.map((s) => (
                <StepBadge key={s.key} step={s} />
              ))}
            </ol>
          </details>
          <ol className="hidden space-y-1 lg:block">
            {onboardingSteps.map((s) => (
              <StepBadge key={s.key} step={s} />
            ))}
          </ol>
        </aside>

        <div className="min-w-0 space-y-4">
          {onboardingSteps.map((s) => (
            <StepCard
              key={s.key}
              step={s}
              hospital={hospital}
              sources={sources}
              philosophies={philosophies}
              hospitalId={id}
              loading={loading}
              onChanged={refresh}
            />
          ))}
          <section aria-labelledby="post-onboarding-title" className="mt-8 border-t border-slate-300 pt-6">
            <h2 id="post-onboarding-title" className="text-lg font-bold text-slate-900">온보딩 이후 정기 운영 성과</h2>
            <p className="mt-1 text-sm text-slate-600">첫 발행과 첫 AI 답변 언급률 측정은 공개 운영 시작 이후의 성과이며 온보딩 완료를 막지 않습니다.</p>
            <div className="mt-4 space-y-4">
              {outcomeSteps.map((step) => (
                <StepCard key={step.key} step={step} hospital={hospital} sources={sources} philosophies={philosophies} hospitalId={id} loading={loading} onChanged={refresh} />
              ))}
            </div>
          </section>
        </div>
      </section>
    </div>
  )
}

function StepBadge({ step }: { step: StepDef }) {
  const tone =
    step.status === 'completed'
      ? 'bg-green-50 text-green-700 border-green-200'
      : step.status === 'current'
        ? 'bg-blue-50 text-blue-700 border-blue-200'
        : step.status === 'locked'
          ? 'bg-amber-50 text-amber-800 border-amber-200'
          : 'bg-slate-50 text-slate-500 border-slate-200'
  const mark = step.status === 'completed' ? '✓' : step.status === 'current' ? '●' : step.status === 'locked' ? '✕' : '○'
  return (
    <li>
      <a
        href={`#step-${step.index}`}
        className={`flex min-w-0 items-start gap-3 rounded-lg border px-3 py-2 ${tone} transition`}
      >
        <span className="text-lg leading-none">{mark}</span>
        <span className="min-w-0">
          <span className="block text-xs font-semibold">{step.phase === 'onboarding' ? `온보딩 ${step.index + 1}` : `후속 성과 ${step.index - 7}`}</span>
          <span className="block break-words text-sm font-medium">{step.title}</span>
        </span>
      </a>
    </li>
  )
}

function StepCard({
  step,
  hospital,
  sources,
  philosophies,
  hospitalId,
  loading,
  onChanged,
}: {
  step: StepDef
  hospital: Hospital | null
  sources: Source[]
  philosophies: Philosophy[]
  hospitalId: string
  loading: boolean
  onChanged: () => void
}) {
  const tone =
    step.status === 'completed'
      ? 'border-green-200'
      : step.status === 'current'
        ? 'border-blue-300 ring-1 ring-blue-200'
        : 'border-slate-200'

  return (
    <details id={`step-${step.index}`} open={step.key === 'processing' || step.status === 'current'} className={`group overflow-hidden rounded-2xl border ${tone} bg-white`}>
      <summary className="flex min-h-20 cursor-pointer list-none items-start justify-between gap-3 px-4 py-4 sm:px-6 sm:py-5 [&::-webkit-details-marker]:hidden">
        <div>
          <p className="text-xs font-semibold text-blue-600">
            {step.phase === 'onboarding' ? `온보딩 ${step.index + 1} / 8` : `후속 성과 ${step.index - 7} / 2`}
          </p>
          <h2 className="mt-1 text-lg font-bold text-slate-900">{step.title}</h2>
          <p className="mt-1 text-sm text-slate-600 max-w-2xl">{step.description}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StepStatusChip status={step.status} />
          <span aria-hidden className="text-xs text-slate-500 transition-transform group-open:rotate-180">▼</span>
        </div>
      </summary>

      <div className="border-t border-slate-100 px-4 py-5 sm:px-6">
        {step.key === 'handoff' && <HandoffStepBody />}
        {step.key === 'profile' && (
          <ProfileStepBody
            hospital={hospital}
            hospitalId={hospitalId}
            sources={sources}
            onChanged={onChanged}
          />
        )}
        {step.key === 'processing' && (
          <div className="space-y-6">
            <SourcesStepBody
              hospital={hospital}
              hospitalId={hospitalId}
              sources={sources}
              onChanged={onChanged}
              loading={loading}
            />
            <ProcessingStepBody hospitalId={hospitalId} sources={sources} onChanged={onChanged} />
          </div>
        )}
        {step.key === 'philosophy_approved' && (
          <PhilosophyStepBody
            hospitalId={hospitalId}
            philosophies={philosophies}
            sources={sources}
            mode="approve"
          />
        )}
        {(['v0', 'site', 'live', 'schedule', 'first_publish', 'sov'] as const).includes(
          step.key as 'v0' | 'site' | 'live' | 'schedule' | 'first_publish' | 'sov',
        ) && (
          <OperationalStepBody step={step} />
        )}
      </div>
    </details>
  )
}

function OperationalStepBody({ step }: { step: StepDef }) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-slate-700">
        {step.status === 'completed'
          ? '필수 정보가 실제로 저장되어 이 단계가 완료된 것을 확인했습니다.'
          : step.status === 'locked'
            ? '앞 필수 단계가 끝나야 이 단계를 진행할 수 있습니다.'
            : '이 단계 화면에서 안내하는 필수 항목을 확인하고 완료해 주세요.'}
      </p>
      {step.href && (
        <Link
          href={step.href}
          className="inline-flex shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:border-blue-200 hover:text-blue-700"
        >
          {step.status === 'completed' ? '상태 확인' : '단계 진행'} →
        </Link>
      )}
    </div>
  )
}

function HandoffStepBody() {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-slate-700">계약 정보와 담당 AE가 일치하는지 확인한 뒤 고객 인수 기록을 승인합니다.</p>
      <Link
        href="/leads"
        className="inline-flex min-h-11 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:border-blue-200 hover:text-blue-700"
      >
        인수 대기열 확인
      </Link>
    </div>
  )
}

function StepStatusChip({ status }: { status: StepDef['status'] }) {
  const map = {
    completed: { label: '완료', cls: 'bg-green-100 text-green-700' },
    current: { label: '진행 필요', cls: 'bg-blue-100 text-blue-700' },
    upcoming: { label: '대기', cls: 'bg-slate-100 text-slate-500' },
    locked: { label: '잠김(선행 단계 필요)', cls: 'bg-amber-50 text-amber-800' },
  } as const
  const { label, cls } = map[status]
  return (
    <span className={`shrink-0 rounded-full px-3 py-1 text-xs font-semibold ${cls}`}>{label}</span>
  )
}

function ProfileStepBody({
  hospital,
  hospitalId,
  sources,
  onChanged,
}: {
  hospital: Hospital | null
  hospitalId: string
  sources: Source[]
  onChanged: () => void
}) {
  return (
    <div className="space-y-5">
      <div className="space-y-3">
        <ul className="text-sm text-slate-700 space-y-1">
          <li>· 필수 병원 정보 완료: {hospital?.profile_complete ? '✓' : '미완료'}</li>
          <li>· 화면의 필수 항목을 모두 입력하고 저장이 완료되어야 합니다.</li>
        </ul>
        <Link
          href={`/hospitals/${hospitalId}/profile`}
          className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
        >
          병원 기본 정보 화면으로 →
        </Link>
      </div>
      <ClinicVisualForm
        hospital={hospital}
        hospitalId={hospitalId}
        sources={sources}
        onSaved={onChanged}
      />
    </div>
  )
}

const VISUAL_STATUS_STYLE: Record<ClinicVisualItem['status'], string> = {
  done: 'bg-green-100 text-green-700',
  needed: 'bg-amber-100 text-amber-800',
  optional: 'bg-slate-100 text-slate-600',
}

const VISUAL_STATUS_LABEL: Record<ClinicVisualItem['status'], string> = {
  done: '승인됨',
  needed: '승인 필요',
  optional: '선택',
}

/**
 * 공개 표면 시각 요소를 병원 기본 정보 단계 안에서 바로 승인한다.
 *
 * 별도 단계를 만들지 않고 기존 프로파일 단계에 붙여, AE가 온보딩을 벗어나지 않고
 * 로고·대표색 하나·첫 문장·정보 우선순위를 확정할 수 있게 한다.
 */
function ClinicVisualForm({
  hospital,
  hospitalId,
  sources,
  onSaved,
}: {
  hospital: Hospital | null
  hospitalId: string
  sources: Source[]
  onSaved: () => void
}) {
  const [logoUrl, setLogoUrl] = useState('')
  const [primaryColor, setPrimaryColor] = useState('')
  const [heroHeadline, setHeroHeadline] = useState('')
  const [heroDescription, setHeroDescription] = useState('')
  const [accessMode, setAccessMode] = useState('')
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLogoUrl(hospital?.logo_url ?? '')
    setPrimaryColor(hospital?.brand_primary_color ?? '')
    setHeroHeadline(hospital?.hero_headline ?? '')
    setHeroDescription(hospital?.hero_description ?? '')
    setAccessMode(hospital?.site_access_mode ?? '')
  }, [hospital])

  const photoCount = sources.filter((source) => isPhotoSourceType(source.source_type)).length
  const checklist = buildClinicVisualChecklist({
    logo_url: logoUrl,
    brand_primary_color: primaryColor,
    hero_headline: heroHeadline,
    hero_description: heroDescription,
    site_access_mode: accessMode,
    photo_count: photoCount,
  })

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setFeedback(null)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/profile`, {
        method: 'PATCH',
        body: JSON.stringify({
          logo_url: logoUrl.trim() || null,
          brand_primary_color: primaryColor.trim() || null,
          hero_headline: heroHeadline.trim() || null,
          hero_description: heroDescription.trim() || null,
          site_access_mode: accessMode || null,
        }),
      })
      setFeedback('공개 표면 시각 요소를 저장했습니다. 다음 사이트 갱신부터 반영됩니다.')
      onSaved()
    } catch (e: unknown) {
      setError(
        e instanceof ApiError && e.message
          ? e.message
          : safeOperatorError('onboarding', '입력값을 확인한 뒤 다시 저장해 주세요.'),
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={save} className="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div>
        <h3 className="text-sm font-bold text-slate-900">공개 표면 시각 요소</h3>
        <p className="mt-1 text-xs leading-5 text-slate-600">
          공식 로고와 대표색 <strong>하나</strong>만 승인하면 나머지 밝기 단계와 대비 안전 색상은 공개 화면이 파생합니다.
          실사진은 필수가 아니며, 없어도 정보 중심으로 정상 노출됩니다.
        </p>
      </div>

      <ul className="flex flex-wrap gap-2">
        {checklist.map((item) => (
          <li
            key={item.key}
            className={`rounded-full px-3 py-1 text-xs font-semibold ${VISUAL_STATUS_STYLE[item.status]}`}
            title={item.hint}
          >
            {item.label} · {VISUAL_STATUS_LABEL[item.status]}
          </li>
        ))}
      </ul>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="text-sm font-medium text-slate-700">
          공식 로고 이미지 URL
          <input
            type="url"
            value={logoUrl}
            onChange={(e) => setLogoUrl(e.target.value)}
            placeholder="https://.../logo.png"
            className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          />
        </label>
        <label className="text-sm font-medium text-slate-700">
          승인된 대표색 1개
          <span className="mt-1.5 flex items-center gap-2">
            <input
              type="color"
              value={primaryColor || '#17365D'}
              onChange={(e) => setPrimaryColor(e.target.value.toUpperCase())}
              className="h-10 w-12 rounded border border-slate-300 bg-white p-1"
              aria-label="대표색 선택"
            />
            <input
              type="text"
              value={primaryColor}
              onChange={(e) => setPrimaryColor(e.target.value)}
              placeholder="#17365D"
              pattern="#[0-9A-Fa-f]{6}"
              className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm"
            />
          </span>
        </label>
      </div>

      <label className="block text-sm font-medium text-slate-700">
        첫 화면 정보 우선순위
        <select
          value={accessMode}
          onChange={(e) => setAccessMode(e.target.value)}
          className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">병원 정보로 자동 선택</option>
          <option value="urgent">당일·야간 진료형 — 시간·전화 우선</option>
          <option value="appointment">예약·방문형 — 위치·상담 우선</option>
          <option value="specialist">전문 진료형 — 의료진·진료 분야 우선</option>
        </select>
      </label>

      <label className="block text-sm font-medium text-slate-700">
        첫 화면 카피
        <textarea
          value={heroHeadline}
          onChange={(e) => setHeroHeadline(e.target.value)}
          maxLength={160}
          rows={2}
          placeholder={'예: 오늘도 문 여는 동네 주치의\n증상과 진료 정보를 방문 전에 확인하세요'}
          className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm leading-6"
        />
        <span className="mt-1 block text-xs font-normal text-slate-500">
          의료광고 금지 표현이 있으면 저장되지 않습니다.
        </span>
      </label>

      <label className="block text-sm font-medium text-slate-700">
        첫 화면 설명
        <textarea
          value={heroDescription}
          onChange={(e) => setHeroDescription(e.target.value)}
          maxLength={320}
          rows={2}
          placeholder="환자가 방문 전에 알아야 할 사실을 짧게 적어 주세요."
          className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm leading-6"
        />
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={saving}
          className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? '저장 중…' : '시각 요소 저장'}
        </button>
        <Link
          href={`/hospitals/${hospitalId}/profile`}
          className="text-sm font-semibold text-slate-600 underline-offset-2 hover:underline"
        >
          아트 디렉션·대표 이미지까지 편집
        </Link>
      </div>

      {feedback && <p className="text-sm font-semibold text-green-700">{feedback}</p>}
      {error && <p className="text-sm font-semibold text-red-700">{error}</p>}
    </form>
  )
}

function SourcesStepBody({
  hospital,
  hospitalId,
  sources,
  onChanged,
  loading,
}: {
  hospital: Hospital | null
  hospitalId: string
  sources: Source[]
  onChanged: () => void
  loading: boolean
}) {
  return (
    <div className="space-y-5">
      <ProfileUrlCandidates hospital={hospital} hospitalId={hospitalId} sources={sources} onChanged={onChanged} />
      <CrawlForm hospitalId={hospitalId} onCreated={onChanged} />
      <NaverBlogBulkForm hospitalId={hospitalId} onCreated={onChanged} />
      <UploadForm hospitalId={hospitalId} onCreated={onChanged} />
      <SourcesList hospitalId={hospitalId} sources={sources} loading={loading} onChanged={onChanged} />
    </div>
  )
}

function ProfileUrlCandidates({
  hospital,
  hospitalId,
  sources,
  onChanged,
}: {
  hospital: Hospital | null
  hospitalId: string
  sources: Source[]
  onChanged: () => void
}) {
  const [addingKey, setAddingKey] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const candidates = getProfileUrlCandidates(hospital, sources)

  async function addCandidate(candidate: ProfileUrlCandidate) {
    setAddingKey(candidate.key)
    setFeedback(null)
    try {
      const result = await addProfileUrlCandidate(fetchAPI, hospitalId, candidate)
      if (result === 'crawled') {
        setFeedback(`${candidate.title} 자료를 크롤했습니다.`)
        onChanged()
      }
    } catch (e: unknown) {
      setFeedback(
        e instanceof ApiError
          ? e.message
          : safeOperatorError('onboarding', '입력 내용을 확인한 뒤 자료 추가를 다시 누르세요.'),
      )
    } finally {
      setAddingKey(null)
    }
  }

  if (candidates.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
        병원 기본 정보에 추가할 공식 주소 후보가 없습니다.
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50 p-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-bold text-blue-950">병원 기본 정보에 추가할 공식 주소</h3>
        <p className="text-xs text-blue-700">
          병원 기본 정보에 입력된 공식 채널을 다시 입력하지 않고 자료로 가져올 수 있습니다.
        </p>
      </div>
      <ul className="mt-3 space-y-2">
        {candidates.map((candidate) => (
          <li
            key={candidate.key}
            className="flex flex-col gap-2 rounded-lg border border-blue-100 bg-white p-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-900">{candidate.title}</p>
              <p className="truncate text-xs text-slate-500">{candidate.url}</p>
            </div>
            {isProfileOnlyCandidate(candidate.key) ? (
              <span className="shrink-0 text-xs font-semibold text-blue-800">이미 병원 기본 정보에 있습니다</span>
            ) : (candidate.registeredCount ?? 0) > 0 ? (
              <span className="shrink-0 text-xs font-semibold text-blue-800">등록됨(하위 페이지 {candidate.registeredCount}건)</span>
            ) : (
              <button
                type="button"
                onClick={() => addCandidate(candidate)}
                disabled={addingKey === candidate.key}
                className="shrink-0 rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {addingKey === candidate.key ? '추가 중...' : '자료로 추가'}
              </button>
            )}
          </li>
        ))}
      </ul>
      {feedback && <p className="mt-2 text-xs text-blue-800">{feedback}</p>}
    </div>
  )
}

function CrawlForm({ hospitalId, onCreated }: { hospitalId: string; onCreated: () => void }) {
  const [type, setType] = useState('HOMEPAGE')
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setFeedback(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/crawl`, {
        method: 'POST',
        body: JSON.stringify({ source_type: type, title, url }),
      })
      setTitle('')
      setUrl('')
      setFeedback('URL 크롤 완료. 본문이 자동 추출됐습니다.')
      onCreated()
    } catch (e: unknown) {
      setFeedback(safeOperatorError('onboarding', '현재 병원 주소를 확인한 뒤 다시 추가를 누르세요.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-3">
      <h3 className="text-sm font-bold text-slate-900">URL로 자료 추가 (자동 크롤)</h3>
      <div className="grid gap-2 md:grid-cols-[160px_1fr]">
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          {SOURCE_TYPE_OPTIONS.filter((o) => o.group === 'TEXT').map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <input
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="자료 제목 (예: 병원 공식 홈페이지)"
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        />
      </div>
      <input
        required
        type="url"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://..."
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
      />
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? '크롤 중…' : 'URL 자동 크롤'}
        </button>
        {feedback && <span className="text-xs text-slate-600">{feedback}</span>}
      </div>
    </form>
  )
}

function UploadForm({ hospitalId, onCreated }: { hospitalId: string; onCreated: () => void }) {
  const [type, setType] = useState('PHOTO_DOCTOR')
  const [assetKind, setAssetKind] = useState('')
  const [title, setTitle] = useState('')
  const [files, setFiles] = useState<FileList | null>(null)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  const isPhotoType = isPhotoSourceType(type)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!files || files.length === 0) return
    setBusy(true)
    setFeedback(null)

    const results: { success: boolean; filename: string }[] = []
    const totalFiles = files.length
    let anyPublicPhotoSucceeded = false

    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i]
        try {
          const fd = new FormData()
          fd.append('source_type', type)
          // 제목이 비어 있으면 파일명 사용 (백엔드가 자동 처리)
          fd.append('title', title.trim())
          fd.append('file', file)
          if (isPhotoSourceType(type)) {
            fd.append('is_public', 'true')
            fd.append('asset_kind', assetKind)
          }
          // 일괄 업로드 시 모든 파일에 skip_revalidate=true (마지막 후 명시적 revalidate)
          const url = `/admin/hospitals/${hospitalId}/essence/sources/upload${
            totalFiles > 1 ? '?skip_revalidate=true' : ''
          }`
          await fetchAPI(url, {
            method: 'POST',
            body: fd,
          })
          results.push({ success: true, filename: file.name })
          if (isPhotoType) {
            anyPublicPhotoSucceeded = true
          }
        } catch (e: unknown) {
          results.push({ success: false, filename: file.name })
        }
      }

      // 공개 사진이 하나라도 성공했으면 마지막 파일 실패 여부와 무관하게 revalidate 1회
      if (anyPublicPhotoSucceeded && totalFiles > 1) {
        try {
          await fetchAPI(`/admin/hospitals/${hospitalId}/essence/revalidate`, {
            method: 'POST',
          })
        } catch (e: unknown) {
          // revalidate 실패는 무시 (_safe 패턴)
        }
      }

      const successCount = results.filter((r) => r.success).length
      const failCount = results.filter((r) => !r.success).length

      if (successCount > 0 && failCount === 0) {
        if (isPhotoType) {
          setFeedback(`${successCount}개 사진 저장 완료 — 병원 사이트에 표시됩니다.`)
        } else {
          setFeedback(`${successCount}개 파일 업로드 완료.`)
        }
      } else if (successCount > 0 && failCount > 0) {
        setFeedback(`${successCount}개 성공, ${failCount}개 실패. 실패한 파일을 다시 선택해 업로드하세요.`)
      } else {
        setFeedback(safeOperatorError('onboarding', `${totalFiles}개 파일 업로드 실패. 선택한 파일과 유형을 확인한 뒤 다시 저장하세요.`))
      }

      if (successCount > 0) {
        setTitle('')
        setFiles(null)
        const inp = document.getElementById('upload-file') as HTMLInputElement | null
        if (inp) inp.value = ''
        onCreated()
      }
    } catch (e: unknown) {
      setFeedback(safeOperatorError('onboarding', '선택한 파일과 유형을 확인한 뒤 다시 저장하세요.'))
    } finally {
      setBusy(false)
    }
  }

  const selectedFileCount = files?.length ?? 0
  const acceptTypes = isPhotoType
    ? 'image/*'
    : 'image/*,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document'

  return (
    <form onSubmit={submit} className="rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-3">
      <h3 className="text-sm font-bold text-slate-900">파일 업로드 (사진 / PDF / DOCX)</h3>
      <div className="grid gap-2 md:grid-cols-[200px_1fr]">
        <select
          value={type}
          onChange={(e) => {
            setType(e.target.value)
            setAssetKind('')
          }}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <optgroup label="사진">
            {SOURCE_TYPE_OPTIONS.filter((o) => o.group === 'PHOTO').map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </optgroup>
          <optgroup label="문서">
            {SOURCE_TYPE_OPTIONS.filter((o) => o.group === 'TEXT').map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </optgroup>
        </select>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={isPhotoType ? '제목 (비워 두면 파일명 사용)' : '자료 제목'}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        />
      </div>
      {isPhotoType ? (
        <div className="space-y-1.5">
          <label htmlFor="upload-asset-kind" className="block text-sm font-medium text-slate-700">
            이미지 성격과 허용 용도
          </label>
          <select
            id="upload-asset-kind"
            required
            value={assetKind}
            onChange={(e) => setAssetKind(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          >
            <option value="">확인 후 선택</option>
            {photoAssetKindOptions(type).map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <p className="text-xs leading-5 text-slate-600">
            실제 인물로 확인된 사진만 의료진 영역에 노출됩니다. 생성 이미지와 캐릭터는 콘텐츠 삽화로만 사용할 수 있습니다.
          </p>
        </div>
      ) : null}
      {isPhotoType && <p className="text-xs text-slate-600">여러 장을 한 번에 고를 수 있습니다</p>}
      <input
        id="upload-file"
        required
        type="file"
        multiple={isPhotoType}
        accept={acceptTypes}
        onChange={(e) => setFiles(e.target.files)}
        className="block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm"
      />
      {selectedFileCount > 1 && (
        <p className="text-xs text-slate-600">{selectedFileCount}개 파일 선택됨. 모두 같은 유형으로 저장됩니다.</p>
      )}
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy || !files || files.length === 0 || (isPhotoType && !assetKind)}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? '업로드 중…' : selectedFileCount > 1 ? `${selectedFileCount}개 업로드` : '업로드'}
        </button>
        {feedback && <span className="text-xs text-slate-600">{feedback}</span>}
      </div>
    </form>
  )
}

function SourcesList({
  hospitalId,
  sources,
  loading,
  onChanged,
}: {
  hospitalId: string
  sources: Source[]
  loading: boolean
  onChanged: () => void
}) {
  const [excludingId, setExcludingId] = useState<string | null>(null)
  const [excludeErrors, setExcludeErrors] = useState<Record<string, string>>({})
  const [pendingPublicId, setPendingPublicId] = useState<string | null>(null)
  const [pendingAssetKindId, setPendingAssetKindId] = useState<string | null>(null)
  const [publicErrors, setPublicErrors] = useState<Record<string, string>>({})

  async function togglePublic(sourceId: string, next: boolean) {
    setPendingPublicId(sourceId)
    setPublicErrors((prev) => {
      const errors = { ...prev }
      delete errors[sourceId]
      return errors
    })
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/${sourceId}/public`, {
        method: 'PATCH',
        body: JSON.stringify({ is_public: next }),
      })
      onChanged()
    } catch (e: unknown) {
      const message = safeOperatorError('onboarding', '사진 공개 여부를 다시 저장하세요.')
      setPublicErrors((prev) => ({ ...prev, [sourceId]: message }))
    } finally {
      setPendingPublicId(null)
    }
  }

  async function updatePhotoAssetKind(source: Source, assetKind: string) {
    setPendingAssetKindId(source.id)
    try {
      const approvedUsage = assetKind === 'VERIFIED_REAL_PERSON'
        ? ['DOCTOR_IDENTITY']
        : assetKind === 'VERIFIED_FACILITY'
          ? ['HERO', 'GALLERY']
          : ['CONTENT_EDITORIAL']
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/${source.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          source_metadata: {
            ...source.source_metadata,
            asset_kind: assetKind,
            approved_usage: approvedUsage,
          },
        }),
      })
      onChanged()
    } catch (e: unknown) {
      const message = safeOperatorError('onboarding', '이미지 성격과 허용 용도를 다시 저장하세요.')
      setPublicErrors((prev) => ({ ...prev, [source.id]: message }))
    } finally {
      setPendingAssetKindId(null)
    }
  }

  async function exclude(sourceId: string) {
    if (!confirm('이 자료를 제외하시겠습니까? 운영 기준 초안과 병원 공개 페이지에서 빠집니다.')) return
    setExcludingId(sourceId)
    setExcludeErrors((prev) => {
      const next = { ...prev }
      delete next[sourceId]
      return next
    })
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/${sourceId}/exclude`, {
        method: 'POST',
      })
      onChanged()
    } catch (e: unknown) {
      const message = safeOperatorError('onboarding', '자료 목록을 다시 불러온 뒤 제외를 다시 누르세요.')
      setExcludeErrors((prev) => ({ ...prev, [sourceId]: message }))
    } finally {
      setExcludingId(null)
    }
  }

  if (loading && sources.length === 0) {
    return <p className="text-sm text-slate-500">자료 목록을 불러오는 중…</p>
  }
  if (sources.length === 0) {
    return (
      <p className="text-sm text-slate-500 italic">
        아직 등록된 자료가 없습니다. 위 폼으로 첫 자료를 추가해 주세요.
      </p>
    )
  }
  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        등록된 자료 ({sources.length})
      </p>
      <ul className="space-y-2">
        {sources.map((s) => {
          const fileHref = s.file_access_url ?? s.file_url
          return (
            <li
              key={s.id}
              className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-white p-3 text-sm"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 font-medium text-slate-900">
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
                      {sourceTypeLabel(s)}
                    </span>
                    <span className="truncate">{s.title}</span>
                  </p>
                  <p className="mt-1 text-xs text-slate-500 truncate">
                    {s.url ? (
                      <a href={s.url} target="_blank" rel="noopener" className="inline-flex min-h-11 items-center underline">{s.url}</a>
                    ) : fileHref ? (
                      <a
                        href={fileHref}
                        target="_blank"
                        rel="noopener"
                        className="underline"
                      >
                        파일 보기 ({sourceFileFormatLabel(s.mime_type)})
                      </a>
                    ) : (
                      '본문 직접 입력'
                    )}
                    {s.evidence_note_count > 0 && ` · 근거 노트 ${s.evidence_note_count}개`}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {isPhotoSourceType(s.source_type) && (
                    <div className="flex flex-col items-end gap-2">
                    <select
                      value={typeof s.source_metadata.asset_kind === 'string' ? s.source_metadata.asset_kind : ''}
                      disabled={pendingAssetKindId === s.id}
                      onChange={(e) => updatePhotoAssetKind(s, e.target.value)}
                      aria-label={`${s.title} 이미지 성격과 허용 용도`}
                      className="max-w-64 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-xs"
                    >
                      <option value="">사용 전 확인 필요</option>
                      {photoAssetKindOptions(s.source_type).map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                    <label className="flex min-h-11 cursor-pointer items-center gap-2">
                      <span
                        className={`rounded-full px-2 py-1 text-xs font-semibold ${
                          s.is_public
                            ? 'bg-blue-100 text-blue-700'
                            : 'bg-slate-100 text-slate-600'
                        }`}
                      >
                        {s.is_public ? '공개' : '비공개'}
                      </span>
                      <input
                        type="checkbox"
                        checked={s.is_public}
                        disabled={pendingPublicId === s.id}
                        onChange={(e) => togglePublic(s.id, e.target.checked)}
                        aria-label={`${s.title} 공개 사이트 표시`}
                        className="rounded border-slate-300"
                      />
                    </label>
                    </div>
                  )}
                  <span
                    className={`rounded-full px-2 py-1 text-xs font-semibold ${
                      s.status === 'PROCESSED'
                        ? 'bg-green-100 text-green-700'
                        : s.status === 'ERROR'
                          ? 'bg-red-100 text-red-700'
                          : s.status === 'EXCLUDED'
                            ? 'bg-slate-100 text-slate-500'
                            : 'bg-yellow-100 text-yellow-800'
                    }`}
                  >
                    {sourceStatusLabel(s)}
                  </span>
                  {s.status !== 'EXCLUDED' && (
                    <button
                      onClick={() => exclude(s.id)}
                      disabled={excludingId === s.id}
                      className="min-h-11 rounded border border-slate-300 bg-white px-3 py-2 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                    >
                      {excludingId === s.id ? '제외 중…' : '제외'}
                    </button>
                  )}
                </div>
              </div>
              {excludeErrors[s.id] && (
                <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-700">{excludeErrors[s.id]}</p>
              )}
              {publicErrors[s.id] && (
                <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-700">{publicErrors[s.id]}</p>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function ProcessingStepBody({
  hospitalId,
  sources,
  onChanged,
}: {
  hospitalId: string
  sources: Source[]
  onChanged: () => void
}) {
  const pending = sources.filter((s) => s.status === 'PENDING' && (s.raw_text?.trim() ?? '').length > 0)
  const processed = sources.filter((s) => s.status === 'PROCESSED')
  const errored = sources.filter((s) => s.status === 'ERROR' && hasProcessableText(s))
  const blocked = sources.filter((s) => !!getProcessingBlockReason(s))
  const excluded = sources.filter((s) => s.status === 'EXCLUDED')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkTracking, setBulkTracking] = useState(false)
  const [processingActive, setProcessingActive] = useState(false)
  const [elapsedSeconds, setElapsedSeconds] = useState(0)
  const [bulkFeedback, setBulkFeedback] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!processingActive) {
      setElapsedSeconds(0)
      return
    }
    const timer = window.setInterval(() => setElapsedSeconds((value) => value + 1), 1000)
    return () => window.clearInterval(timer)
  }, [processingActive])

  useEffect(() => {
    if (!bulkTracking) return
    if (pending.length === 0) {
      setBulkTracking(false)
      setProcessingActive(false)
      setBulkFeedback('대기 자료 처리가 끝났습니다. 완료·오류 상태를 확인해 주세요.')
      return
    }
    const timer = window.setInterval(onChanged, 5000)
    return () => window.clearInterval(timer)
  }, [bulkTracking, onChanged, pending.length])

  async function process(sourceId: string) {
    if (busyId || bulkTracking) return
    setBusyId(sourceId)
    setElapsedSeconds(0)
    setProcessingActive(true)
    setErrors((prev) => {
      const next = { ...prev }
      delete next[sourceId]
      return next
    })
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/${sourceId}/process`, {
        method: 'POST',
      })
      onChanged()
    } catch (e: unknown) {
      const message = safeOperatorError('onboarding', '해당 자료의 ‘다시 처리’를 누르고, 계속 실패하면 개발팀 문의용 정보를 복사하세요.')
      setErrors((prev) => ({ ...prev, [sourceId]: message }))
    } finally {
      setBusyId(null)
      setProcessingActive(false)
    }
  }

  async function processAllPending() {
    setBulkBusy(true)
    setBulkFeedback(null)
    try {
      const result = (await fetchAPI(
        `/admin/hospitals/${hospitalId}/essence/sources/process-pending?limit=50`,
        { method: 'POST' },
      )) as { queued: number }
      setBulkFeedback(
        result.queued > 0
          ? `${result.queued}개 자료를 처리 대기열에 넣었습니다. 잠시 뒤 상태를 새로 확인해 주세요.`
          : '처리할 대기 자료가 없습니다.',
      )
      if (result.queued > 0) {
        setBulkTracking(true)
        setElapsedSeconds(0)
        setProcessingActive(true)
        window.setTimeout(onChanged, 2500)
      }
    } catch (e: unknown) {
      setBulkFeedback(safeOperatorError('onboarding', '오류 자료 다시 처리를 누르고, 계속 실패하면 개발팀 문의용 정보를 복사하세요.'))
    } finally {
      setBulkBusy(false)
    }
  }

  if (sources.length === 0) {
    return <p className="text-sm text-slate-500">먼저 자료를 인입해 주세요.</p>
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-700">
        처리 가능: <strong>{pending.length}</strong>개 · 완료: <strong>{processed.length}</strong>개 ·
        오류: <strong>{errored.length}</strong>개 · 차단: <strong>{blocked.length}</strong>개 · 제외: <strong>{excluded.length}</strong>개
      </p>
      {(busyId || bulkTracking) && (
        <div role="status" className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
          근거를 추출하는 중입니다 · {elapsedSeconds}초 경과
          <span className="mt-1 block text-xs text-blue-700">자료 1건은 보통 1~2분 걸립니다. 완료될 때까지 다른 처리 버튼은 잠깁니다.</span>
        </div>
      )}
      {pending.length > 1 && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-blue-200 bg-blue-50 p-3">
          <button
            type="button"
            onClick={processAllPending}
            disabled={bulkBusy || bulkTracking || busyId !== null}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {bulkBusy ? '일괄 처리 요청 중…' : bulkTracking ? '일괄 처리 진행 중…' : `대기 자료 ${pending.length}개 일괄 처리`}
          </button>
          {bulkFeedback && <span className="text-xs text-blue-900">{bulkFeedback}</span>}
        </div>
      )}
      {processed.length > 0 && pending.length === 0 && errored.length === 0 && blocked.length === 0 && (
        <div className="rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">
          근거 추출이 완료됐습니다. 운영 기준 초안 생성 단계로 진행할 수 있습니다.
        </div>
      )}
      {pending.length > 0 && (
        <ul className="space-y-2">
          {pending.map((s) => (
            <li
              key={s.id}
              className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-white p-3 text-sm"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="truncate">{s.title}</span>
                <button
                  onClick={() => process(s.id)}
                  disabled={busyId !== null || bulkTracking}
                  className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {busyId === s.id ? '처리 중…' : '처리'}
                </button>
              </div>
              {errors[s.id] && (
                <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-700">{errors[s.id]}</p>
              )}
            </li>
          ))}
        </ul>
      )}
      {processed.length > 0 && (
        <p className="text-xs text-slate-500">
          이미 처리된 자료는 운영 기준 초안에 자동으로 반영됩니다.
        </p>
      )}
      {blocked.length > 0 && (
        <div className="space-y-2 rounded-lg border border-yellow-200 bg-yellow-50 p-3">
          <p className="text-xs font-semibold text-yellow-900">근거 추출할 수 없는 자료</p>
          <ul className="space-y-2">
            {blocked.map((s) => (
              <li key={s.id} className="flex flex-col gap-2 rounded bg-white p-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-slate-900">{s.title}</span>
                  <button
                    type="button"
                    disabled
                    title={getProcessingBlockReason(s) ?? undefined}
                    className="rounded bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-400"
                  >
                    근거 추출 불가
                  </button>
                </div>
                <p className="text-yellow-900">{getProcessingBlockReason(s)}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {errored.length > 0 && (
        <div className="space-y-2 rounded-lg border border-red-200 bg-red-50 p-3">
          <p className="text-xs font-semibold text-red-800">처리 중 오류가 발생한 자료 — 재시도 가능</p>
          <ul className="space-y-2">
            {errored.map((s) => (
              <li key={s.id} className="flex flex-col gap-1 rounded bg-white p-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-slate-900">{s.title}</span>
                  <button
                    onClick={() => process(s.id)}
                    disabled={busyId !== null || bulkTracking}
                    className="rounded bg-red-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-red-700 disabled:opacity-50"
                  >
                    {busyId === s.id ? '재시도 중…' : '다시 처리'}
                  </button>
                </div>
                {(errors[s.id] || s.process_error) && (
                  <p className="whitespace-pre-line text-red-700">{errors[s.id] || safeOperatorError('onboarding', '이 자료의 ‘다시 처리’를 누르세요.')}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function PhilosophyStepBody({
  hospitalId,
  philosophies,
  sources,
  mode,
}: {
  hospitalId: string
  philosophies: Philosophy[]
  sources: Source[]
  mode: 'draft' | 'approve'
}) {
  const draft = philosophies.find((p) => p.status === 'DRAFT')
  const approved = philosophies.find((p) => p.status === 'APPROVED')

  if (mode === 'draft') {
    if (approved) {
      return (
        <p className="text-sm text-slate-700">
          이미 승인된 운영 기준이 있습니다 (v{approved.version}).{' '}
          <Link href={`/hospitals/${hospitalId}/essence`} className="text-blue-600 underline">
            essence 화면에서 보기 →
          </Link>
        </p>
      )
    }
    if (draft) {
      return (
        <div className="space-y-3 text-sm">
          <p className="text-slate-700">
            <strong>v{draft.version}</strong> 초안이 준비됐습니다.
          </p>
          {draft.positioning_statement && (
            <p className="rounded-lg bg-blue-50 px-3 py-2 text-blue-800 italic">
              {draft.positioning_statement}
            </p>
          )}
          <Link
            href={`/hospitals/${hospitalId}/essence`}
            className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
          >
            essence 화면에서 검토 →
          </Link>
        </div>
      )
    }
    const processedCount = sources.filter((s) => s.status === 'PROCESSED').length
    if (processedCount === 0) {
      return <p className="text-sm text-slate-500">먼저 자료를 처리해 주세요.</p>
    }
    return (
      <p className="text-sm text-slate-700">
        처리된 자료 {processedCount}개로 운영 기준을 자동 준비하고 있습니다.{' '}
        <Link href={`/hospitals/${hospitalId}/essence`} className="text-blue-600 underline">
          운영 기준 진행 상태 보기 →
        </Link>
      </p>
    )
  }

  // mode === 'approve'
  if (approved) {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-green-700 font-semibold">
          ✓ 운영 기준 v{approved.version} 승인 완료
        </p>
        <Link
          href={`/hospitals/${hospitalId}/schedule`}
          className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
        >
          콘텐츠 스케줄 설정으로 →
        </Link>
      </div>
    )
  }
  if (draft) {
    return (
      <p className="text-sm text-slate-700">
        AI 안전검수가 v{draft.version} 초안을 보류했습니다.{' '}
        <Link href={`/hospitals/${hospitalId}/essence`} className="text-blue-600 underline">
          예외 근거만 확인 →
        </Link>
      </p>
    )
  }
  return <p className="text-sm text-slate-500">자료 처리가 끝나면 운영 기준이 자동으로 준비됩니다.</p>
}
