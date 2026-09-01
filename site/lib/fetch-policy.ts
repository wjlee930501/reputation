// 공개 표면 전체가 공유하는 ISR 주기. 페이지의 `export const revalidate`와 이 파일이
// 만드는 fetch()의 `next.revalidate`가 서로 다른 값이면 더 짧은 쪽이 실효값이 되어
// 페이지 선언이 무의미해진다 — 한 상수로 양쪽을 맞춘다.
export const REVALIDATE_SECONDS = 1800

export function publicFetchInit(revalidateSeconds: number = REVALIDATE_SECONDS): RequestInit {
  if (process.env.NODE_ENV === 'development') {
    return { cache: 'no-store' }
  }
  return { next: { revalidate: revalidateSeconds } } as RequestInit
}
