export type OnboardingStepKey =
  | 'profile'
  | 'v0'
  | 'site'
  | 'live'
  | 'sources'
  | 'processing'
  | 'philosophy_draft'
  | 'philosophy_approved'
  | 'schedule'
  | 'first_publish'
  | 'sov'

export interface OnboardingStep {
  key: OnboardingStepKey
  index: number
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
  nextActionHref: string
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

export function deriveOnboardingSteps(
  hospital: LifecycleHospital | null,
  sources: LifecycleSource[],
  philosophies: LifecyclePhilosophy[],
  readiness: LifecycleReadiness | null,
  hospitalId: string,
): OnboardingStep[] {
  const includedSources = sources.filter(isIncludedEvidenceSource)
  const hasSource = includedSources.length > 0
  const allIncludedSourcesProcessed = hasSource && includedSources.every((source) => source.status === 'PROCESSED')
  const draftReady = philosophies.some((item) => item.status === 'DRAFT' || item.status === 'APPROVED')
  const approved = philosophies.some((item) => item.status === 'APPROVED')
  const essenceFresh = readiness?.essence?.source_stale === false
  const approvedCurrent = approved && readiness?.essence?.approved_philosophy_exists !== false && essenceFresh

  const definitions: Array<Omit<OnboardingStep, 'index' | 'status'> & { done: boolean }> = [
    {
      key: 'profile',
      title: '병원 프로파일 입력',
      description: '필수 병원·원장·진료·공식 채널 정보를 검증하고 완료합니다.',
      href: `/hospitals/${hospitalId}/profile`,
      done: Boolean(hospital?.profile_complete) && readinessCheck(readiness, 'core_profile') !== false,
    },
    {
      key: 'v0',
      title: 'V0 진단 리포트',
      description: '초기 AI 답변 노출 진단과 PDF 생성 완료를 확인합니다.',
      href: `/hospitals/${hospitalId}/reports`,
      done: Boolean(hospital?.v0_report_done) && readinessCheck(readiness, 'v0_report') !== false,
    },
    {
      key: 'site',
      title: '병원 정보 허브 준비',
      description: '공개 허브 빌드가 완료됐는지 확인합니다.',
      href: `/hospitals/${hospitalId}/profile#domain-setup`,
      done: Boolean(hospital?.site_built) && readinessCheck(readiness, 'site_built') !== false,
    },
    {
      key: 'live',
      title: '기본 주소 또는 자기 도메인 LIVE',
      description: '공개 주소를 명시적으로 활성화하고 실제 노출 상태를 확인합니다.',
      href: `/hospitals/${hospitalId}/profile#domain-setup`,
      done: Boolean(hospital?.site_live) && readinessCheck(readiness, 'domain') !== false,
    },
    {
      key: 'sources',
      title: '병원 자산 인입',
      description: '홈페이지 URL, 인터뷰 PDF/DOCX 등 근거 자료를 추가합니다.',
      done: hasSource,
    },
    {
      key: 'processing',
      title: '포함 자료 전체 처리',
      description: '제외하지 않은 모든 근거 자료의 처리를 완료합니다.',
      done: allIncludedSourcesProcessed && readinessCheck(readiness, 'essence_sources') !== false,
    },
    {
      key: 'philosophy_draft',
      title: '운영 기준 초안 검토',
      description: '처리된 근거로 콘텐츠 운영 기준 초안을 생성·검토합니다.',
      href: `/hospitals/${hospitalId}/essence`,
      done: draftReady,
    },
    {
      key: 'philosophy_approved',
      title: '현재 자료 기준 운영 기준 승인',
      description: '현재 자료 스냅샷과 일치하는 운영 기준을 승인합니다.',
      href: `/hospitals/${hospitalId}/essence`,
      done: approvedCurrent && readinessCheck(readiness, 'essence_freshness') !== false,
    },
    {
      key: 'schedule',
      title: '콘텐츠 스케줄 설정',
      description: '요금제와 발행 요일을 저장하고 월간 슬롯을 생성합니다.',
      href: `/hospitals/${hospitalId}/schedule`,
      done: Boolean(hospital?.schedule_set) && readinessCheck(readiness, 'schedule') !== false,
    },
    {
      key: 'first_publish',
      title: '첫 콘텐츠 발행',
      description: '검수된 콘텐츠를 최소 1편 실제 공개합니다.',
      href: `/hospitals/${hospitalId}/content`,
      done: (readiness?.published_content_count ?? 0) > 0 && readinessCheck(readiness, 'published_content') !== false,
    },
    {
      key: 'sov',
      title: 'AI 답변 언급률 측정',
      description: '실제 측정 기록이 저장됐는지 확인합니다.',
      href: `/hospitals/${hospitalId}/dashboard`,
      done: (readiness?.sov_record_count ?? 0) > 0 && readinessCheck(readiness, 'sov_data') !== false,
    },
  ]

  const firstIncomplete = definitions.findIndex((item) => !item.done)
  return definitions.map((item, index) => ({
    key: item.key,
    index,
    title: item.title,
    description: item.description,
    href: item.href,
    status: item.done ? 'completed' : index === firstIncomplete ? 'current' : 'upcoming',
  }))
}

export function deriveOnboardingSummary(
  steps: OnboardingStep[],
  readiness: LifecycleReadiness | null,
): OnboardingSummary {
  const allHardGatesDone = steps.every((step) => step.status === 'completed')
  if (allHardGatesDone && readiness?.status === 'READY') {
    return {
      stateLabel: '운영 준비 완료',
      stateClassName: 'bg-green-100 text-green-800',
      headline: '신규 병원 온보딩의 모든 하드 게이트가 통과됐습니다.',
      detail: 'LIVE, 최신 운영 기준, 스케줄, 첫 발행, AI 답변 언급률 측정까지 실제 데이터로 확인했습니다.',
      nextActionLabel: '운영 대시보드 확인',
      nextActionHref: steps.find((step) => step.key === 'sov')?.href ?? '#',
      blockedReason: null,
    }
  }

  const current = steps.find((step) => step.status === 'current')
  if (!current) {
    return {
      stateLabel: '검증 필요',
      stateClassName: 'bg-amber-100 text-amber-900',
      headline: '단계는 완료됐지만 백엔드 운영 준비 판정이 남아 있습니다.',
      detail: '운영 준비도 검사에서 실패한 항목을 확인해 주세요.',
      nextActionLabel: '운영 대시보드 확인',
      nextActionHref: '#',
      blockedReason: 'readiness 상태가 READY가 아닙니다.',
    }
  }

  return {
    stateLabel: '다음 작업',
    stateClassName: 'bg-blue-100 text-blue-800',
    headline: `${current.title} 단계가 필요합니다.`,
    detail: current.description,
    nextActionLabel: current.title,
    nextActionHref: current.href ?? `#step-${current.index}`,
    blockedReason: null,
  }
}
