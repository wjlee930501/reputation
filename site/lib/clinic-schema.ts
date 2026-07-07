// 병원 허브 JSON-LD 빌더 공통 유틸 — page.tsx가 쓰는 순수 함수만 모은다.
// (테스트 가능하도록 DOM/네트워크 의존 없이 입력→출력만 다룬다.)
//
// FAQPage JSON-LD 빌더는 여기 두지 않는다 — schema.ts의 buildFaqPageJsonLd가
// @id/url까지 채운 완성판이고 실제로 쓰이는 것도 그쪽이다 (중복 방지).

/**
 * hospital.region([시/도, 구/시])로 PostalAddress를 보강한다.
 * region[0] → addressRegion, region[1] → addressLocality. 비어 있으면 해당 키를 생략한다.
 * streetAddress/addressCountry는 호출부가 채운다 (이 함수는 지역 파생값만 더한다).
 */
export function buildAddressRegionFields(
  region: string[] | null | undefined,
): { addressRegion?: string; addressLocality?: string } {
  const cleaned = (region || []).map((r) => (r || '').trim()).filter(Boolean)
  const fields: { addressRegion?: string; addressLocality?: string } = {}
  if (cleaned[0]) fields.addressRegion = cleaned[0]
  if (cleaned[1]) fields.addressLocality = cleaned[1]
  return fields
}
