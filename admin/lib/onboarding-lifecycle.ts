import { type ClinicVisualInput, missingClinicVisualItems } from './clinic-visual-readiness.ts'

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
  status: 'completed' | 'current' | 'upcoming' | 'locked'
  /**
   * 단계 제목 옆에 붙는 짧은 알림.
   *
   * 7단계는 자동 검수가 보류한 초안이 쌓여 있어도 목록에서는 다른 단계와 똑같이
   * 보였다. 그래서 운영자는 운영 기준 화면을 직접 열어 보기 전까지 처리할 초안이
   * 몇 건 있는지 알 수 없었다.
   */
  badge?: string
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

export interface LifecycleHospital extends ClinicVisualInput {
  /**
   * 아직 자료로 가져오지 않은 공식 채널 수.
   *
   * 처리 완료 판정은 "제외하지 않은 자료"만 센다. 그래서 등록조차 안 된 공식 블로그는
   * 애초에 분모에 들어가지 않고, 채널이 비어 있는 채로 단계가 `완료`가 됐다(O-8).
   * 완료를 되돌리지는 않는다 — 가져오지 않은 채널이 있다는 사실을 함께 알린다.
   */
  unimported_channel_count?: number | null
  profile_complete?: boolean | null
  v0_report_done?: boolean | null
  site_built?: boolean | null
  site_live?: boolean | null
  schedule_set?: boolean | null
}

export interface LifecycleSource {
  source_type: string
  status: string
  raw_text?: string | null
}

export interface LifecyclePhilosophy {
  status: string
  version?: number | null
}

/**
 * 승인 대기 중인 운영 기준 초안 수.
 *
 * 이미 승인된 버전보다 새로운 DRAFT만 센다. 승인된 v3이 운영 중일 때 남아 있는 옛
 * v1·v2 초안은 처리할 일이 아니므로 알림에 넣으면 없는 작업을 만들어 낸다.
 */
export function countPendingPhilosophyDrafts(philosophies: LifecyclePhilosophy[]): number {
  const rows = Array.isArray(philosophies) ? philosophies : []
  const approvedVersions = rows
    .filter((item) => item.status === 'APPROVED')
    .map((item) => item.version ?? 0)
  const latestApproved = approvedVersions.length > 0 ? Math.max(...approvedVersions) : null

  return rows.filter(
    (item) =>
      item.status === 'DRAFT' && (latestApproved === null || (item.version ?? 0) > latestApproved),
  ).length
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
  /**
   * PDF 파일까지 만들어진 **초기 진단(V0)** 리포트 수.
   *
   * 행만 있고 PDF가 없으면 0이고, 월간 리포트 PDF는 여기 들어오지 않는다 — 초기
   * 진단을 건너뛴 병원이 월간 PDF 덕에 3단계 완료로 보이면 안 된다.
   */
  v0_report_pdf_count?: number | null
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
  return source.status !== 'EXCLUDED'
    && !source.source_type.startsWith('PHOTO_')
    && Boolean(source.raw_text?.trim())
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
  // 공개 표면 시각 승인(로고·대표색·첫 화면 카피·정보 우선순위)은 프로파일 단계
  // 안에서 처리한다. 승인이 남아 있는데 단계가 완료로 보이면 8/8이 사실과 달라지므로
  // 완료 판정에 함께 넣는다. 사진은 blocksApproval=false라 여기 들어오지 않는다.
  const pendingVisualApprovals = missingClinicVisualItems(hospital ?? {})
  const includedSources = sources.filter(isIncludedEvidenceSource)
  const hasSource = includedSources.length > 0
  const allIncludedSourcesProcessed = hasSource && includedSources.every((source) => source.status === 'PROCESSED')
  const approved = philosophies.some((item) => item.status === 'APPROVED')
  const approvedCurrent = approved
    && readiness?.essence?.approved_philosophy_exists !== false
    && readiness?.essence?.source_stale === false

  const pendingDraftCount = countPendingPhilosophyDrafts(philosophies)
  const unimportedChannels = Math.max(0, hospital?.unimported_channel_count ?? 0)

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
      description: pendingVisualApprovals.length > 0
        ? `필수 병원·원장·진료·공식 채널 정보를 검증하고, 남은 공개 표면 시각 승인 ${pendingVisualApprovals.length}건(${pendingVisualApprovals.map((item) => item.label).join(', ')})을 마칩니다.`
        : '필수 병원·원장·진료·공식 채널 정보를 검증하고 공개 표면 시각 요소를 승인합니다.',
      href: `/hospitals/${hospitalId}/profile`,
      done: Boolean(hospital?.profile_complete)
        && readinessCheck(readiness, 'core_profile') !== false
        && pendingVisualApprovals.length === 0,
    },
    {
      key: 'v0',
      phase: 'onboarding',
      title: '초기 진단 리포트',
      description: readiness?.v0_report_pdf_count === 0
        ? '초기 AI 답변 노출 진단 리포트 PDF가 아직 없습니다. 월간 리포트가 아니라 초기 진단 PDF까지 만들어야 이 단계가 끝납니다.'
        : '초기 AI 답변 노출 진단과 PDF 생성을 확인합니다.',
      href: `/hospitals/${hospitalId}/dashboard#v0-measurement-runs`,
      // 단계 설명이 초기 진단 + PDF를 요구하므로 완료 판정도 둘을 본다. 측정만 끝나고
      // PDF 생성이 실패한 병원을 완료로 표시하면 원장 보고 자료가 없는 채로 넘어가고,
      // 종류를 가리지 않으면 월간 PDF가 초기 진단을 대신해 버린다.
      done: Boolean(hospital?.v0_report_done)
        && readinessCheck(readiness, 'v0_report') !== false
        && readiness?.v0_report_pdf_count !== 0,
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
      description: unimportedChannels > 0
        ? `병원 근거 자료를 추가하고 제외하지 않은 모든 자료의 처리를 완료합니다. 아직 가져오지 않은 공식 채널이 ${unimportedChannels}건 있습니다 — 가져오지 않으면 처리 대상에서 빠집니다.`
        : '병원 근거 자료를 추가하고 제외하지 않은 모든 자료의 처리를 완료합니다.',
      done: allIncludedSourcesProcessed && readinessCheck(readiness, 'essence_sources') !== false,
      badge: unimportedChannels > 0 ? `미수집 공식 채널 ${unimportedChannels}건` : undefined,
    },
    {
      key: 'philosophy_approved',
      phase: 'onboarding',
      title: '콘텐츠 운영 기준 자동 준비',
      description: 'AI 이중 검수와 안전 규칙으로 현재 근거 자료에 맞는 운영 기준을 자동 준비합니다.',
      href: `/hospitals/${hospitalId}/essence`,
      done: approvedCurrent && readinessCheck(readiness, 'essence_freshness') !== false,
      badge: pendingDraftCount > 0 ? `승인 대기 초안 ${pendingDraftCount}건` : undefined,
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
  const steps: OnboardingStep[] = definitions.map((item, index) => ({
    key: item.key,
    index,
    phase: item.phase,
    title: item.title,
    description: item.description,
    href: item.href,
    badge: item.badge,
    // 완료 사실과 진행 순서는 별개다. V0처럼 앞 단계가 막혀 있어도 AE가 이미
    // 끝낸 자료 처리·운영 기준·스케줄을 다시 '대기'로 되돌려 표시하지 않는다.
    status: item.done
      ? 'completed'
      : index === firstIncomplete
        ? 'current'
        : item.phase === 'onboarding' && firstIncomplete >= 0 && index > firstIncomplete
          ? 'locked'
          : 'upcoming',
  }))
  const processingStep = steps.find((item) => item.key === 'processing')
  if (processingStep) {
    processingStep.href = `/hospitals/${hospitalId}/onboarding#step-${processingStep.index}`
  }
  return steps
}

export function deriveOnboardingSummary(
  steps: OnboardingStep[],
  _readiness: LifecycleReadiness | null,
): OnboardingSummary {
  const onboardingSteps = steps.filter((step) => step.phase === 'onboarding')
  const currentOnboarding = onboardingSteps.find((step) => step.status === 'current')

  if (currentOnboarding) {
    // 뒤 단계가 이미 다 끝난 병원에 "다음 단계로 진행할 수 없습니다"라고 하면 인과가
    // 뒤집힌다 — 이미 운영 중인데 나중에 생긴 관문이 소급 적용된 경우다(O-3).
    // 그때는 막힌 게 아니라 품질 보완이 남은 것이므로 그렇게 말한다.
    // 소급으로 세워진 관문은 공개 표면 시각 승인(profile)뿐이다. 인수 승인 같은
    // 진짜 선행 조건까지 이 문구를 쓰면 정반대로 안심시키게 된다.
    const laterSteps = onboardingSteps.slice(onboardingSteps.indexOf(currentOnboarding) + 1)
    const isRetroactive =
      currentOnboarding.key === 'profile'
      && laterSteps.length > 0
      && laterSteps.every((step) => step.status === 'completed')

    if (isRetroactive) {
      return {
        stateLabel: '보완 필요',
        stateClassName: 'bg-amber-100 text-amber-800',
        headline: `이미 운영 중인 병원입니다. ${currentOnboarding.title}에 남은 승인을 마쳐 주세요.`,
        detail: currentOnboarding.description,
        nextActionLabel: currentOnboarding.title,
        nextActionHref: currentOnboarding.href ?? null,
        blockedReason:
          '공개 사이트는 이미 서비스 중이고 뒤 단계도 끝났습니다. 다만 나중에 추가된 '
          + '공개 화면 품질 기준이 아직 승인되지 않아, 그만큼 기본값으로 노출되고 있습니다.',
      }
    }

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
