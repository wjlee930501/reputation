// Next의 CSS, JavaScript, image optimizer는 검색 렌더러가 페이지를 이해하는 데 필요하다.
// 공개 번들에는 병원 비공개 데이터가 들어가지 않으므로 /_next/를 막지 않는다.
export const ROBOTS_DISALLOWED_PATHS = ['/api/', '/.well-known/']

// 이미지 프록시 경로만 /api/ 차단보다 긴 allow 규칙으로 공개한다.
export const ROBOTS_IMAGE_PROXY_ALLOW = [
  '/api/v1/public/hospitals/*/assets/',
  '/api/v1/public/hospitals/*/contents/*/image',
]

export const ROBOTS_ALLOWED_PATHS = ['/', ...ROBOTS_IMAGE_PROXY_ALLOW]
