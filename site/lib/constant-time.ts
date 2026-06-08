import { createHash, timingSafeEqual } from 'node:crypto'

// 길이가 달라도 누설이 없도록 양쪽을 SHA-256으로 고정 길이 다이제스트로 만든 뒤
// 상수 시간 비교한다. (backend core/security.py의 X-Admin-Key 비교와 동일한 방식)
export function constantTimeEqual(provided: string, secret: string): boolean {
  const a = createHash('sha256').update(provided).digest()
  const b = createHash('sha256').update(secret).digest()
  return timingSafeEqual(a, b)
}
