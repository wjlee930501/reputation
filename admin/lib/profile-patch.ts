/**
 * 프로파일 PATCH 본문을 섹션별 허용 목록으로 만든다.
 *
 * 화면은 병원 상세 응답 전체를 폼 상태로 들고 있다. 그걸 그대로 보내면 사실 칸을
 * 저장할 때 다른 섹션이 방금 저장한 브랜드 값까지 오래된 스냅샷으로 덮어쓴다.
 * 각 섹션은 자기 칸만 보낸다. `profile_complete`는 서버가 파생하고, `logo_url`은
 * 업로드 엔드포인트가 소유하므로 어느 목록에도 없다.
 */

/** 사실 섹션이 편집하는 칸. backend HospitalProfileUpdate의 같은 이름 필드와 짝이다. */
export const FACTS_PATCH_FIELDS = [
  // 원장 표시값(director_name·director_career)은 서버가 대표 의료진에서 파생한다 —
  // 폼이 들고 있는 헤더 스냅샷을 되돌려 보내면 방금 바꾼 의료진 목록과 어긋난다.
  // `physicians`도 여기 없다 — 집합 전체 교체라 목록을 읽은 화면만(physiciansLoaded)
  // 자기 payload에 실어 보낸다. 아직 못 읽은 화면이 빈 목록으로 지우지 않게 한다.
  'director_philosophy',
  'address',
  'address_detail',
  'phone',
  'business_hours',
  'website_url',
  'blog_url',
  'kakao_channel_url',
  'google_business_profile_url',
  'google_maps_url',
  'naver_place_url',
  'latitude',
  'longitude',
  'wikidata_qid',
  'gbp_place_id',
  'naver_place_id',
  'kakao_place_id',
  'hira_org_id',
  'region',
  'specialties',
  'keywords',
  'competitors',
  'treatments',
] as const

/**
 * 브랜드 섹션이 편집하는 칸. 로고는 업로드가 소유하므로 빠져 있고,
 * `hero_image_url`은 사진 목록의 '대표 이미지로 지정'이 소유한다.
 */
export const BRAND_PATCH_FIELDS = [
  'brand_primary_color',
  'brand_accent_color',
  'hero_media_kind',
  'hero_headline',
  'hero_description',
  'image_style_direction',
  'site_access_mode',
] as const

function pick(profile: object, fields: readonly string[]): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(profile).filter(([field]) => fields.includes(field)),
  )
}

export function factsPatchPayload(profile: object): Record<string, unknown> {
  return pick(profile, FACTS_PATCH_FIELDS)
}

export function brandPatchPayload(profile: object): Record<string, unknown> {
  return pick(profile, BRAND_PATCH_FIELDS)
}

/** 한 폼이 사실과 브랜드를 함께 편집하는 예전 프로파일 화면 전용. */
export function profilePatchPayload(profile: object): Record<string, unknown> {
  return { ...factsPatchPayload(profile), ...brandPatchPayload(profile) }
}
