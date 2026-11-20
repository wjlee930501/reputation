/**
 * 섹션 설정 미리보기의 사진·로고 주소(`/api/v1/public/site-preview/{token}/…`)인가.
 *
 * 이런 주소는 Next 이미지 최적화기로 보내지 않는다(`<Image unoptimized>`) — 최적화기는 원본이
 * no-store여도 결과를 `public, max-age=86400`으로 내보내 토큰이 만료된 뒤에도 사진을 내준다.
 * 프록시도 최적화기 요청에 이 주소가 들면 거절한다(`proxy.ts`).
 *
 * 프록시 검사는 손으로 만든 주소를 막아야 하므로, 최적화기·백엔드가 실제로 보는 경로로 판정한다.
 * 최적화기처럼 `new URL`로 해석해 탭·줄바꿈 제거, `..`·역슬래시 정리를 거친 뒤, 백엔드처럼 경로를
 * 한 번 풀어(`%73ite-preview` → `site-preview`) 미리보기 경로인지 본다. 쿼리·조각은 보지 않는다
 * (정상 사진 주소의 `?label=site-preview`를 막지 않게). 경로를 반복해서 풀지 않는다 — 백엔드도 한
 * 번만 풀고, 반복하면 정상 `%25` 파일명이 깨진다. 해석할 수 없는 주소는 막는다(최적화기도 거절한다).
 */
const PREVIEW_PATH = /\/public\/site-preview(?:\/|$)/i

export function isPreviewAssetUrl(url: string | null | undefined): boolean {
  if (typeof url !== 'string') return false
  let pathname: string
  try {
    pathname = new URL(url, 'http://image.local').pathname
  } catch {
    return true
  }
  if (PREVIEW_PATH.test(pathname)) return true
  let decoded: string
  try {
    decoded = decodeURIComponent(pathname)
  } catch {
    return false // 경로를 풀 수 없으면 백엔드도 미리보기 경로로 보지 않는다.
  }
  return PREVIEW_PATH.test(decoded.replace(/\\/g, '/'))
}
