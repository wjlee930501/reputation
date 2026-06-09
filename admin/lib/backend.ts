// BACKEND_URL 해석 — site/lib/config.ts의 getApiBase와 동일 정책.
// 프로덕션에서 env 누락 시 localhost로 조용히 폴백하면 배포 설정 실수가
// 진단하기 어려운 런타임 장애로 바뀐다. 요청 시점에 명시적으로 실패시킨다.
export function getBackendUrl(): string {
  const url = (process.env.BACKEND_URL || '').trim()
  if (process.env.NODE_ENV === 'production') {
    if (!url) {
      throw new Error('BACKEND_URL must be set in production')
    }
    if (url.includes('localhost') || url.includes('127.0.0.1')) {
      throw new Error('BACKEND_URL must not point to localhost in production')
    }
    return url
  }
  return url || 'http://localhost:8000'
}
