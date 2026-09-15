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
  addressDetail?: string | null,
): Record<string, string> | undefined {
  const road = (address || '').trim()
  if (!road) return undefined
  // 상세 주소(건물명·층·호)는 별도 schema.org 필드가 없다. streetAddress는 "거리
  // 주소 전체"를 담는 필드이므로 도로명 뒤에 이어 붙이는 것이 표준에 맞고, 지도·답변
  // 엔진이 실제로 찾아올 수 있는 주소가 된다.
  const detail = (addressDetail || '').trim()
  return {
    '@type': 'PostalAddress',
    streetAddress: detail ? `${road} ${detail}` : road,
    addressCountry: 'KR',
  }
}

/** 화면에 한 줄로 적는 전체 주소. 구조화 데이터의 streetAddress와 같은 값을 만든다. */
export function fullClinicAddress(
  address: string | null | undefined,
  addressDetail?: string | null,
): string {
  const road = (address || '').trim()
  const detail = (addressDetail || '').trim()
  if (!road) return ''
  return detail ? `${road} ${detail}` : road
}
