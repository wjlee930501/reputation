/**
 * 외부 채널 URL 칸의 중복 검사.
 *
 * 구글 항목이 두 칸으로 나뉘어 있는데(관리용 병원 정보 URL, 환자가 보는 지도 URL)
 * 라벨만으로는 무엇이 무엇인지 알 수 없어서, 운영자가 같은 지도 링크를 두 칸에 그대로
 * 붙여 넣었다. 두 값은 공개 표면에서 각각 다른 링크로 나가므로, 같은 값이 들어가면
 * 환자에게 같은 링크가 두 번 보이고 AI가 참고하는 정보도 한쪽이 비어 있는 상태가 된다.
 *
 * 저장을 막지는 않는다 — 실제로 같은 URL을 쓰는 병원이 있을 수 있으므로, 사실만 알린다.
 */

export interface ExternalChannelUrls {
  google_business_profile_url?: string | null
  google_maps_url?: string | null
  naver_place_url?: string | null
  kakao_channel_url?: string | null
  website_url?: string | null
  blog_url?: string | null
}

const FIELD_LABELS: Record<keyof ExternalChannelUrls, string> = {
  google_business_profile_url: '구글 병원 정보 URL',
  google_maps_url: '구글 지도 URL',
  naver_place_url: '네이버 플레이스 URL',
  kakao_channel_url: '카카오 채널 URL',
  website_url: '홈페이지 URL',
  blog_url: '블로그 URL',
}

/** 비교용 정규화 — 프로토콜·www·마지막 슬래시 차이는 같은 주소로 본다. */
export function normalizeChannelUrl(value: string | null | undefined): string | null {
  const raw = (value ?? '').trim()
  if (!raw) return null
  const withoutScheme = raw.replace(/^https?:\/\//i, '')
  const withoutWww = withoutScheme.replace(/^www\./i, '')
  const withoutTrailingSlash = withoutWww.replace(/\/+$/, '')
  return withoutTrailingSlash.toLowerCase() || null
}

/**
 * 같은 주소가 들어간 칸 묶음마다 한 줄씩 알린다.
 *
 * 두 칸 이상이 같은 값일 때만 알리며, 어떤 칸들이 겹쳤는지 이름으로 말한다.
 */
export function findDuplicateChannelUrls(profile: ExternalChannelUrls): string[] {
  const grouped = new Map<string, string[]>()
  for (const field of Object.keys(FIELD_LABELS) as Array<keyof ExternalChannelUrls>) {
    const normalized = normalizeChannelUrl(profile[field])
    if (!normalized) continue
    grouped.set(normalized, [...(grouped.get(normalized) ?? []), FIELD_LABELS[field]])
  }

  return [...grouped.values()]
    .filter((labels) => labels.length > 1)
    .map((labels) => `${labels.join(' · ')}에 같은 주소가 들어 있습니다. 칸마다 다른 주소를 넣거나, 해당하지 않는 칸은 비워 두세요.`)
}

/** 두 구글 칸이 무엇을 담는 칸인지 화면에서 직접 말한다. */
export const GOOGLE_CHANNEL_FIELD_HINTS = {
  google_business_profile_url:
    '병원이 관리하는 구글 병원 정보 관리 화면 주소입니다. 환자에게 보여 주는 링크가 아닙니다.',
  google_maps_url: '환자가 길찾기에 쓰는 공개 지도 주소입니다. 공개 표면의 지도 링크가 됩니다.',
} as const
