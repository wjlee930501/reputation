import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../app/hospitals/[id]/content/page.tsx', import.meta.url), 'utf8')
// 표본 판정은 화면이 아니라 content-rows.ts의 canConfirmSample 하나로 모였다.
const rows = readFileSync(new URL('./content-rows.ts', import.meta.url), 'utf8')

test('the post-publish confirm button is gated on the server sample flag', () => {
  assert.match(rows, /post_publish_review_required === true/)
  assert.match(page, /canConfirmSample/)
})

test('brief editing is never offered for published items', () => {
  assert.doesNotMatch(page, /\['DRAFT', 'PUBLISHED'\]\.includes\(selected\.status\)/)
})

// 화면에서 수동 발행이 사라져 발행 게이트 409를 해석할 자리도 없다 —
// 그 조작과 안내는 운영 센터가 맡는다(설계 §4.5).
