export async function persistThenApprove(
  persist: () => Promise<unknown>,
  approve: () => Promise<unknown>,
): Promise<void> {
  await persist()
  await approve()
}

export interface VariantDraft {
  query_text: string
  platform: string
  language: string
  is_active: true
}

export const SUPPORTED_QUERY_PLATFORMS = ['CHATGPT', 'GEMINI'] as const

export function isSupportedQueryPlatform(platform: string): boolean {
  return SUPPORTED_QUERY_PLATFORMS.includes(
    platform.trim().toUpperCase() as (typeof SUPPORTED_QUERY_PLATFORMS)[number],
  )
}

export function buildPlatformVariants(
  questions: string[],
  platforms: string[],
  language: string,
): VariantDraft[] {
  const normalizedPlatforms = Array.from(new Set(platforms.map((platform) => platform.trim().toUpperCase())))
  const unsupported = normalizedPlatforms.filter((platform) => !isSupportedQueryPlatform(platform))
  if (unsupported.length > 0) {
    throw new Error(`지원하지 않는 AI 서비스입니다: ${unsupported.join(', ')}`)
  }
  return questions.flatMap((queryText) =>
    normalizedPlatforms.map((platform) => ({
      query_text: queryText,
      platform,
      language,
      is_active: true as const,
    })),
  )
}

export function canRunMeasurement(
  targets: Array<{ status: string; variants: Array<{ is_active: boolean; platform?: string }> }>,
): boolean {
  return targets.some(
    (target) => target.status === 'ACTIVE' && target.variants.some(
      (variant) => variant.is_active
        && (variant.platform === undefined || isSupportedQueryPlatform(variant.platform)),
    ),
  )
}

export function canSubmitSchedule(existingLoading: boolean, existingError: string | null): boolean {
  return !existingLoading && !existingError
}

export interface ReportReviewState {
  requireOperationalSummaries: boolean
  hasPdf: boolean
  hasSov: boolean
  hasContent: boolean
  hasStrategy: boolean
  hasEssence: boolean
  approvedPhilosophy: boolean
  sourceCount: number
  processedSourceCount: number
  needsReviewCount: number
  missingStandardCount: number
  medicalRiskCount: number
}

export function reportDeliveryBlockers(state: ReportReviewState): string[] {
  return [
    !state.hasPdf ? 'PDF 생성이 끝난 뒤 최종 검수할 수 있습니다.' : null,
    state.requireOperationalSummaries && !state.hasSov ? 'AI 답변 언급률 요약이 없어 측정 결과를 먼저 확인해야 합니다.' : null,
    state.requireOperationalSummaries && !state.hasContent ? '콘텐츠 성과 요약이 없어 발행 콘텐츠 상태를 먼저 확인해야 합니다.' : null,
    state.requireOperationalSummaries && state.hasContent && !state.hasStrategy ? '환자 질문 목표별 운영 전략이 없어 리포트를 다시 생성해야 합니다.' : null,
    state.requireOperationalSummaries && !state.hasEssence ? '운영 기준 요약이 없어 승인된 콘텐츠 운영 기준과 자료 검토 상태를 먼저 확인해야 합니다.' : null,
    state.requireOperationalSummaries && state.hasEssence && !state.approvedPhilosophy ? '승인된 콘텐츠 운영 기준이 없습니다.' : null,
    state.requireOperationalSummaries && state.hasEssence && state.sourceCount > 0 && state.processedSourceCount < state.sourceCount
      ? '검토가 끝나지 않은 병원 자료가 있습니다.'
      : null,
    state.requireOperationalSummaries && state.hasEssence && state.needsReviewCount + state.missingStandardCount > 0
      ? '재검토가 필요한 콘텐츠를 먼저 정리해야 합니다.'
      : null,
    state.requireOperationalSummaries && state.medicalRiskCount > 0
      ? `의료광고 리스크 ${state.medicalRiskCount}건을 먼저 정리해야 합니다.`
      : null,
  ].filter((item): item is string => Boolean(item))
}

export interface ActivationReadiness {
  profile_complete: boolean
  v0_report_done: boolean
  site_built?: boolean
  schedule_set: boolean
}

export function activationBlockers(readiness: ActivationReadiness): string[] {
  return [
    !readiness.profile_complete ? '병원 프로필' : null,
    !readiness.v0_report_done ? '초기 진단 리포트' : null,
    !readiness.site_built ? '병원 정보 허브' : null,
    !readiness.schedule_set ? '콘텐츠 스케줄' : null,
  ].filter((item): item is string => Boolean(item))
}
