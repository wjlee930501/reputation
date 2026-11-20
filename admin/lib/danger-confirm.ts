/**
 * 위험 동작 확인창의 판정(2026-11-20 마케팅 요청 — 일시정지·개인정보 파기·비공개 후 재생성·
 * 계정 정지·전체 중지는 ⋯ 메뉴 안 + 확인창, 되돌리기 어려운 것은 이름 다시 입력).
 *
 * 입력 비교는 앞뒤 공백만 무시한다. 대소문자·가운데 공백까지 맞아야 한다 — 「비슷하면 통과」는
 * 확인창의 목적(잘못 누른 병원을 막기)과 반대다.
 */
export function typedConfirmationMatches(expected: string | null | undefined, typed: string): boolean {
  const target = (expected ?? '').trim()
  if (!target) return true
  return typed.trim() === target
}
