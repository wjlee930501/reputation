/**
 * 온보딩 시각 요소 폼이 서버 값과 언제 다시 맞출지 결정한다.
 *
 * 온보딩 화면은 자료 처리 추적 중 5초마다 병원을 다시 불러오고, 다른 자식 폼의 저장
 * 성공도 같은 새로고침을 부른다. 그때마다 새 병원 객체가 오므로 값이 그대로여도
 * 참조는 바뀐다. 참조를 기준으로 폼을 리셋하면 AE가 입력하던 로고 URL·대표색·첫
 * 화면 카피가 저장 전에 사라진다. 값 비교 + 입력 중 보호를 한 곳에 모아 둔다.
 */

export interface ClinicVisualValues {
  logoUrl: string
  primaryColor: string
  heroHeadline: string
  heroDescription: string
  accessMode: string
}

export interface ClinicVisualSource {
  logo_url?: string | null
  brand_primary_color?: string | null
  hero_headline?: string | null
  hero_description?: string | null
  site_access_mode?: string | null
}

export function clinicVisualValuesOf(hospital: ClinicVisualSource | null): ClinicVisualValues {
  return {
    logoUrl: hospital?.logo_url ?? '',
    primaryColor: hospital?.brand_primary_color ?? '',
    heroHeadline: hospital?.hero_headline ?? '',
    heroDescription: hospital?.hero_description ?? '',
    accessMode: hospital?.site_access_mode ?? '',
  }
}

/** 값이 같으면 같은 문자열 — 참조가 바뀌어도 리셋 대상이 아니라는 판단의 근거. */
export function clinicVisualSignature(values: ClinicVisualValues): string {
  return JSON.stringify([
    values.logoUrl,
    values.primaryColor,
    values.heroHeadline,
    values.heroDescription,
    values.accessMode,
  ])
}

export function shouldSyncFromServer(input: {
  dirty: boolean
  syncedSignature: string
  serverSignature: string
}): boolean {
  if (input.syncedSignature === input.serverSignature) return false
  // 입력 중에는 서버 값이 바뀌어도 덮지 않는다. 저장에 성공하면 dirty가 풀리고
  // 그때 서버가 정규화한 값으로 맞춰진다.
  return !input.dirty
}
