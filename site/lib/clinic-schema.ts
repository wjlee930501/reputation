// 병원 허브 JSON-LD 빌더 공통 유틸 — page.tsx가 쓰는 순수 함수만 모은다.
// (테스트 가능하도록 DOM/네트워크 의존 없이 입력→출력만 다룬다.)
//
// FAQPage JSON-LD 빌더는 여기 두지 않는다 — schema.ts의 buildFaqPageJsonLd가
// @id/url까지 채운 완성판이고 실제로 쓰이는 것도 그쪽이다 (중복 방지).

/** 실제 병원 주소만으로 만드는 PostalAddress.
 *
 * `hospital.region`은 주소 계층이 아니라 검색 타겟 지역 배열이다. 예를 들어
 * `["강남구", "서초구"]`를 addressRegion/addressLocality로 매핑하면 병원 주소와
 * 무관한 행정구역을 물리 주소로 주장하게 된다. 원문 주소는 이미 전체 주소이므로
 * 임의 파싱하거나 타겟 값을 섞지 않고 그대로 제공한다.
 */
export function buildPostalAddress(
  address: string | null | undefined,
): Record<string, string> | undefined {
  const streetAddress = (address || '').trim()
  if (!streetAddress) return undefined
  return {
    '@type': 'PostalAddress',
    streetAddress,
    addressCountry: 'KR',
  }
}
