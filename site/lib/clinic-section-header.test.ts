import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const TREATMENT_GRID = readFileSync(
  join(HERE, '..', 'app', '[slug]', '_components', 'TreatmentGrid.tsx'),
  'utf8',
)

/**
 * P-A-3 — 홈의 첫 섹션 제목이 `sr-only` 헤더 안에 있었다.
 *
 * 다른 섹션은 모두 제목이 보이는데 진료 영역만 보이지 않아서, 화면에서는 카드
 * 네 장이 아무 맥락 없이 시작됐다. 제목이 없으면 그 아래 목록이 무엇의 목록인지
 * 눈으로 읽을 방법이 없다.
 */
test('the home treatment section title is visible, not screen-reader-only', () => {
  const header = TREATMENT_GRID.slice(
    TREATMENT_GRID.indexOf('<header'),
    TREATMENT_GRID.indexOf('</header>'),
  )
  assert.ok(header.length > 0, '진료 영역 섹션 헤더를 찾지 못했습니다')
  assert.doesNotMatch(header, /sr-only/)
  assert.match(header, /<h2 className="clinic-section-title">진료 영역<\/h2>/)
})

test('the home treatment section says how many areas exist in total', () => {
  // 대표 4개만 보여주면서 전체가 몇 개인지 말하지 않으면, 네 개가 전부인 것처럼
  // 읽힌다 (P-C-3).
  assert.match(TREATMENT_GRID, /treatments\.length > lead\.length/)
  assert.match(TREATMENT_GRID, /countLabel\(treatments\.length, '개'\)/)
  assert.match(TREATMENT_GRID, /\$\{hospitalRootUrl\}\/treatments/)
})

test('the treatment grid tells CSS how many columns to draw', () => {
  // 4열 고정이면 진료 항목이 1~3개인 병원에서 테두리만 남은 빈 칸이 생긴다 (P-C-1).
  assert.match(TREATMENT_GRID, /'--clinic-tx-columns': Math\.min\(lead\.length, LEAD_LIMIT\)/)
  assert.match(TREATMENT_GRID, /'--clinic-tx-columns-md': Math\.min\(lead\.length, 2\)/)
})
