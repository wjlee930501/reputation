export type ProfileChecklistStatus = 'done' | 'required' | 'recommended'

export interface ProfileChecklistItem {
  key: string
  label: string
  hint: string
  status: ProfileChecklistStatus
  required: boolean
}

export interface ProfileReadinessInput {
  director_name?: string | null
  director_career?: string | null
  director_philosophy?: string | null
  address?: string | null
  phone?: string | null
  business_hours?: Record<string, string> | null
  website_url?: string | null
  blog_url?: string | null
  google_business_profile_url?: string | null
  google_maps_url?: string | null
  naver_place_url?: string | null
  latitude?: number | null
  longitude?: number | null
  region?: string[] | null
  specialties?: string[] | null
  keywords?: string[] | null
  competitors?: string[] | null
  treatments?: Array<{ name?: string | null }> | null
  aeo_domain?: string | null
  site_built?: boolean | null
}

function trimmed(value: string | null | undefined): string {
  return (value ?? '').trim()
}

function isCoordinateInRange(
  value: number | null | undefined,
  min: number,
  max: number,
): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max
}

export function buildProfileChecklist(profile: ProfileReadinessInput): ProfileChecklistItem[] {
  const hasAnyHour = Object.values(profile.business_hours ?? {}).some((value) => trimmed(value).length > 0)
  const hasNamedTreatment = (profile.treatments ?? []).some((treatment) => trimmed(treatment.name).length > 0)
  const hasCoords =
    isCoordinateInRange(profile.latitude, -90, 90) &&
    isCoordinateInRange(profile.longitude, -180, 180)
  const hasGoogleAsset =
    trimmed(profile.google_maps_url).length > 0 ||
    trimmed(profile.google_business_profile_url).length > 0

  const required: Array<Omit<ProfileChecklistItem, 'status'>> = [
    { key: 'director_basic', label: '원장 기본정보', hint: '원장명과 약력을 모두 입력합니다.', required: true },
    { key: 'director_philosophy', label: '진료 철학', hint: '원장 인터뷰에서 확인한 진료 철학을 한 단락으로 정리합니다.', required: true },
    { key: 'contact', label: '병원 연락처', hint: '주소·전화번호·진료시간(요일 1개 이상)을 모두 채웁니다.', required: true },
    { key: 'web_channels', label: '홈페이지/블로그', hint: '병원 홈페이지 또는 블로그 URL 중 하나는 등록합니다.', required: true },
    {
      key: 'ai_channels',
      label: 'AI가 참고할 외부 채널',
      hint: '네이버 플레이스와 구글 지도/병원 정보 URL을 등록합니다.',
      required: true,
    },
    { key: 'geo', label: '좌표/지역 정보', hint: '위·경도 좌표와 지역 태그를 등록합니다.', required: true },
    { key: 'targeting', label: '전문과목/키워드', hint: '전문과목과 핵심 키워드를 각 1개 이상 등록합니다.', required: true },
    { key: 'treatments', label: '진료 항목', hint: '진료 항목 이름을 1개 이상 등록합니다.', required: true },
  ]

  const done: Record<string, boolean> = {
    director_basic: trimmed(profile.director_name).length > 0 && trimmed(profile.director_career).length > 0,
    director_philosophy: trimmed(profile.director_philosophy).length > 0,
    contact: trimmed(profile.address).length > 0 && trimmed(profile.phone).length > 0 && hasAnyHour,
    web_channels: trimmed(profile.website_url).length > 0 || trimmed(profile.blog_url).length > 0,
    ai_channels: trimmed(profile.naver_place_url).length > 0 && hasGoogleAsset,
    geo: hasCoords && (profile.region ?? []).length > 0,
    targeting: (profile.specialties ?? []).length > 0 && (profile.keywords ?? []).length > 0,
    treatments: hasNamedTreatment,
  }

  const items: ProfileChecklistItem[] = required.map((item) => ({
    ...item,
    status: done[item.key] ? 'done' : 'required',
  }))
  items.push({
    key: 'competitors',
    label: '경쟁 병원',
    hint: 'AI 언급률 비교 대상 병원을 1개 이상 등록하면 리포트 정확도가 올라갑니다.',
    required: false,
    status: (profile.competitors ?? []).length > 0 ? 'done' : 'recommended',
  })
  items.push({
    key: 'domain',
    label: '커스텀 도메인',
    hint: profile.site_built
      ? '병원이 구입한 도메인을 입력하고 DNS 검증까지 완료합니다.'
      : '병원 정보 허브 준비 후 연결합니다. 기본 플랫폼 주소는 별도로 활성화할 수 있습니다.',
    required: false,
    status: trimmed(profile.aeo_domain).length > 0 ? 'done' : 'recommended',
  })
  return items
}

export function missingRequiredProfileItems(profile: ProfileReadinessInput): ProfileChecklistItem[] {
  return buildProfileChecklist(profile).filter((item) => item.required && item.status !== 'done')
}

export function isProfileReady(profile: ProfileReadinessInput): boolean {
  return missingRequiredProfileItems(profile).length === 0
}
