export type OnboardingStepKey =
  | 'handoff'
  | 'profile'
  | 'v0'
  | 'site'
  | 'processing'
  | 'philosophy_approved'
  | 'schedule'
  | 'live'
  | 'first_publish'
  | 'sov'

export type OnboardingPhase = 'onboarding' | 'post_onboarding'

export interface OnboardingStep {
  key: OnboardingStepKey
  index: number
  phase: OnboardingPhase
  title: string
  description: string
  href?: string
  status: 'completed' | 'current' | 'upcoming'
}

export interface OnboardingSummary {
  stateLabel: string
  stateClassName: string
  headline: string
  detail: string
  nextActionLabel: string
  nextActionHref: string | null
  blockedReason: string | null
}

export interface LifecycleHospital {
  profile_complete?: boolean | null
  v0_report_done?: boolean | null
  site_built?: boolean | null
  site_live?: boolean | null
  schedule_set?: boolean | null
}

export interface LifecycleSource {
  source_type: string
  status: string
}

export interface LifecyclePhilosophy {
  status: string
}

export interface LifecycleHandoff {
  state?: string | null
  ae_owner_name?: string | null
  sla_due_at?: string | null
}

export type HandoffDueStatus = {
  readonly label: string
  readonly isOverdue: boolean
}

export interface LifecycleReadiness {
  status?: string | null
  published_content_count?: number | null
  sov_record_count?: number | null
  report_count?: number | null
  essence?: {
    approved_philosophy_exists?: boolean | null
    source_stale?: boolean | null
  } | null
  checks?: Array<{ key: string; passed: boolean }> | null
}

function readinessCheck(readiness: LifecycleReadiness | null, key: string): boolean | null {
  const check = readiness?.checks?.find((item) => item.key === key)
  return check ? check.passed : null
}

function isIncludedEvidenceSource(source: LifecycleSource): boolean {
  return source.status !== 'EXCLUDED' && !source.source_type.startsWith('PHOTO_')
}

export function deriveHandoffDueStatus(
  handoff: LifecycleHandoff | null,
  checkedAt: number | null,
): HandoffDueStatus {
  if (handoff?.state === 'HANDOFF_ACCEPTED') {
    return { label: '인수 완료', isOverdue: false }
  }

  const dueAt = handoff?.sla_due_at ? Date.parse(handoff.sla_due_at) : Number.NaN
  if (checkedAt === null || !Number.isFinite(dueAt)) {
    return { label: '처리 기한 확인 필요', isOverdue: false }
  }
  if (dueAt < checkedAt) {
    return { label: '처리 기한 지남', isOverdue: true }
  }
  return { label: '기한 내 진행 중', isOverdue: false }
}

export function deriveOnboardingSteps(
  hospital: LifecycleHospital | null,
  sources: LifecycleSource[],
  philosophies: LifecyclePhilosophy[],
  readiness: LifecycleReadiness | null,
  hospitalId: string,
  handoff: LifecycleHandoff | null = null,
): OnboardingStep[] {
  const includedSources = sources.filter(isIncludedEvidenceSource)
  const hasSource = includedSources.length > 0
  const allIncludedSourcesProcessed = hasSource && includedSources.every((source) => source.status === 'PROCESSED')
  const approved = philosophies.some((item) => item.status === 'APPROVED')
  const approvedCurrent = approved
    && readiness?.essence?.approved_philosophy_exists !== false
    && readiness?.essence?.source_stale === false

  const definitions: Array<Omit<OnboardingStep, 'index' | 'status'> & { done: boolean }> = [
    {
      key: 'handoff',
      phase: 'onboarding',
      title: '계약 인수',
      description: '담당 AE가 계약 정보와 인수 처리 기한을 확인하고 고객 인수를 승인합니다.',
      href: '/leads',
      done: handoff?.state === 'HANDOFF_ACCEPTED',
    },
    {
      key: 'profile',
      phase: 'onboarding',
      title: '병원 기본 정보 입력',
      description: '필수 병원·원장·진료·공식 채널 정보를 검증하고 완료합니다.',
      href: `/hospitals/${hospitalId}/profile`,
      done: Boolean(hospital?.profile_complete) && readinessCheck(readiness, 'core_profile') !== false,
    },
    {
      key: 'v0',
      phase: 'onboarding',
      title: '초기 진단 리포트',
      description: '초기 AI 답변 노출 진단과 PDF 생성을 확인합니다.',
      href: `/hospitals/${hospitalId}/reports`,
      done: Boolean(hospital?.v0_report_done) && readinessCheck(readiness, 'v0_report') !== false,
    },
    {
      key: 'site',
      phase: 'onboarding',
      title: '콘텐츠 허브 준비',
      description: '승인된 병원 정보와 콘텐츠를 공개 표면이 읽을 수 있는지 확인합니다.',
      href: `/hospitals/${hospitalId}/profile#domain-setup`,
      done: Boolean(hospital?.site_built) && readinessCheck(readiness, 'site_built') !== false,
    },
    {
      key: 'live',
      phase: 'onboarding',
      title: '도메인 확인 및 공개 운영 시작',
      description: '공개 주소와 도메인 상태를 확인한 뒤 공개 운영을 시작합니다.',
      href: `/hospitals/${hospitalId}/profile#domain-setup`,
      done: Boolean(hospital?.site_live) && readinessCheck(readiness, 'domain') !== false,
    },
    {
      key: 'processing',
      phase: 'onboarding',
      title: '근거 자료 수집 및 처리',
      description: '병원 근거 자료를 추가하고 제외하지 않은 모든 자료의 처리를 완료합니다.',
      href: `/hospitals/${hospitalId}/onboarding#step-4`,
      done: allIncludedSourcesProcessed && readinessCheck(readiness, 'essence_sources') !== false,
    },
    {
      key: 'philosophy_approved',
      phase: 'onboarding',
      title: '콘텐츠 운영 기준 자동 준비',
      description: 'AI 이중 검수와 안전 규칙으로 현재 근거 자료에 맞는 운영 기준을 자동 준비합니다.',
      href: `/hospitals/${hospitalId}/essence`,
      done: approvedCurrent && readinessCheck(readiness, 'essence_freshness') !== false,
    },
    {
      key: 'schedule',
      phase: 'onboarding',
      title: '콘텐츠 스케줄 설정',
      description: '요금제와 발행 요일을 저장하고 첫 달 콘텐츠 캘린더를 생성합니다.',
      href: `/hospitals/${hospitalId}/schedule`,
      done: Boolean(hospital?.schedule_set) && readinessCheck(readiness, 'schedule') !== false,
    },
    {
      key: 'first_publish',
      phase: 'post_onboarding',
      title: '첫 콘텐츠 발행',
      description: '온보딩 이후 첫 초안을 검수하고 실제 공개합니다.',
      href: `/hospitals/${hospitalId}/content`,
      done: (readiness?.published_content_count ?? 0) > 0 && readinessCheck(readiness, 'published_content') !== false,
    },
    {
      key: 'sov',
      phase: 'post_onboarding',
      title: '첫 AI 답변 언급률 측정',
      description: '온보딩 이후 첫 ChatGPT·Gemini 측정 기록을 확인합니다.',
      href: `/hospitals/${hospitalId}/dashboard`,
      done: (readiness?.sov_record_count ?? 0) > 0 && readinessCheck(readiness, 'sov_data') !== false,
    },
  ]

  const firstIncomplete = definitions.findIndex((item) => !item.done)
  return definitions.map((item, index) => ({
    key: item.key,
    index,
    phase: item.phase,
    title: item.title,
    description: item.description,
    href: item.href,
    status: index < firstIncomplete || (firstIncomplete === -1 && item.done)
      ? 'completed'
      : index === firstIncomplete
        ? 'current'
        : 'upcoming',
  }))
}

export function deriveOnboardingSummary(
  steps: OnboardingStep[],
  _readiness: LifecycleReadiness | null,
): OnboardingSummary {
  const onboardingSteps = steps.filter((step) => step.phase === 'onboarding')
  const currentOnboarding = onboardingSteps.find((step) => step.status === 'current')

  if (currentOnboarding) {
    return {
      stateLabel: '다음 작업',
      stateClassName: 'bg-blue-100 text-blue-800',
      headline: `${currentOnboarding.title} 단계가 필요합니다.`,
      detail: currentOnboarding.description,
      nextActionLabel: currentOnboarding.title,
      nextActionHref: currentOnboarding.href ?? null,
      blockedReason: currentOnboarding.key === 'handoff'
        ? '담당 AE가 인수 대기열에서 계약 정보와 처리 기한을 확인하고 인수를 승인해야 다음 단계로 진행할 수 있습니다.'
        : `${currentOnboarding.title} 완료 전 다음 온보딩 단계로 진행할 수 없습니다.`,
    }
  }

  const nextOutcome = steps.find((step) => step.phase === 'post_onboarding' && step.status !== 'completed')
  if (nextOutcome) {
    return {
      stateLabel: '온보딩 완료',
      stateClassName: 'bg-green-100 text-green-800',
      headline: '공개 운영 시작까지 온보딩을 완료했습니다.',
      detail: `이제 정기 운영 성과를 시작합니다. 다음 후속 작업은 ${nextOutcome.title}입니다.`,
      nextActionLabel: nextOutcome.title,
      nextActionHref: nextOutcome.href ?? null,
      blockedReason: null,
    }
  }

  const dashboardHref = steps.find((step) => step.key === 'sov')?.href ?? null
  return {
    stateLabel: '정기 운영 중',
    stateClassName: 'bg-green-100 text-green-800',
    headline: '온보딩과 첫 정기 운영 성과를 확인했습니다.',
    detail: '콘텐츠 발행과 AI 답변 언급률 측정을 정기 운영 대시보드에서 관리합니다.',
    nextActionLabel: '운영 대시보드 확인',
    nextActionHref: dashboardHref,
    blockedReason: null,
  }
}
