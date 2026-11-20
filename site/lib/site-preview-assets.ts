/**
 * 섹션 설정 미리보기의 사진·로고 주소(`/api/v1/public/site-preview/{token}/…`)인가.
 *
 * 이런 주소는 Next 이미지 최적화기로 보내지 않는다(`<Image unoptimized>`) — 최적화기는 원본이
 * no-store여도 결과를 `public, max-age=86400`으로 내보내 토큰이 만료된 뒤에도 사진을 내준다.
 * 프록시도 최적화기 요청에 이 주소가 들면 거절한다(`proxy.ts`).
 *
 * 프록시 검사는 손으로 만든 주소를 막아야 한다. `%73ite-preview`나 이중 인코딩(`%2573…`)처럼
 * 글자를 퍼센트로 감싸도 백엔드는 풀어서 미리보기 자산으로 처리하므로, 더 바뀌지 않을 때까지
 * 풀고 소문자로 맞춘 뒤 `site-preview`가 어디든 있으면 미리보기 주소로 본다. 풀 수 없는 주소
 * (깨진 퍼센트 표기)도 미리보기로 본다 — 최적화하지 않을 뿐 사진은 그대로 보인다.
 */
const MAX_DECODE_ROUNDS = 8

export function isPreviewAssetUrl(url: string | null | undefined): boolean {
  if (typeof url !== 'string') return false
  let current = url
  for (let round = 0; round < MAX_DECODE_ROUNDS; round += 1) {
    let decoded: string
    try {
      decoded = decodeURIComponent(current)
    } catch {
      return true
    }
    if (decoded === current) break
    current = decoded
  }
  if (/%[0-9a-f]{2}/i.test(current)) return true // 상한까지 풀어도 남은 인코딩 — 막는다.
  return current.toLowerCase().replace(/\\/g, '/').includes('site-preview')
}
