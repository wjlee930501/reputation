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
  'director_name',
  'director_career',
  'director_philosophy',
  'director_credentials',
  'address',
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

/** 브랜드 섹션이 편집하는 칸. 로고는 업로드가 소유하므로 빠져 있다. */
export const BRAND_PATCH_FIELDS = [
  'brand_primary_color',
  'brand_accent_color',
  'hero_image_url',
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
