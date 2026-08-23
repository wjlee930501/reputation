/**
 * 공개 표면의 개수 표기 (P-C-3).
 *
 * 같은 뜻의 숫자가 화면마다 다른 문장으로 적혀 있었다 — 진료 영역은 `12개 진료 영역`,
 * 관련 글은 `관련 콘텐츠 3편`, 사진은 `등록된 공간 사진 8장 중 대표 6장`.
 * 홈의 대표 진료 4개는 전체가 몇 개인지 아예 말하지 않았다. 세는 규칙이 한곳에
 * 없으면 다음 섹션에서 또 갈라지므로 표기를 여기서 만든다.
 */
export type ClinicCountUnit = '개' | '편' | '장'

export function countLabel(count: number, unit: ClinicCountUnit): string {
  return `${Math.max(0, Math.trunc(count))}${unit}`
}

/**
 * 일부만 보여줄 때의 표기. 전체와 보여준 수가 같으면 null — 같은 수를 두 번 적으면
 * 더 보여줄 것이 있다고 오해하게 된다.
 */
export function previewCountLabel(
  shown: number,
  total: number,
  unit: ClinicCountUnit,
): string | null {
  const safeShown = Math.max(0, Math.trunc(shown))
  const safeTotal = Math.max(safeShown, Math.trunc(total))
  if (safeTotal <= safeShown) return null
  return `전체 ${countLabel(safeTotal, unit)} 중 ${countLabel(safeShown, unit)}`
}
