/**
 * 섹션 설정 미리보기의 사진·로고 주소(`/api/v1/public/site-preview/{token}/…`)인가.
 *
 * 이런 주소는 Next 이미지 최적화기로 보내지 않는다(`<Image unoptimized>`) — 최적화기는 원본이
 * no-store여도 결과를 `public, max-age=86400`으로 내보내 토큰이 만료된 뒤에도 사진을 내준다.
 * 프록시도 최적화기 요청에 이 주소가 들면 거절한다(`proxy.ts`).
 */
export function isPreviewAssetUrl(url: string | null | undefined): boolean {
  return typeof url === 'string' && url.includes('/api/v1/public/site-preview/')
}
