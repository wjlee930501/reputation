import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test } from 'node:test'

// H-04: 수동 초안 합성은 사라졌다. 운영자에게 남는 초안 조작은 재검수 요청과 보관뿐이다.
const essencePageSource = readFileSync(
  join(process.cwd(), 'app/hospitals/[id]/essence/page.tsx'),
  'utf8',
)

test('the essence page no longer calls the manual draft synthesis endpoint', () => {
  assert.equal(essencePageSource.includes('philosophy/draft'), false)
})

test('the essence page offers re-review and archive for a held draft', () => {
  assert.ok(essencePageSource.includes('/re-review'))
  assert.ok(essencePageSource.includes('/archive'))
})

test('the re-review button says the re-synthesis runs off the sources', () => {
  assert.ok(essencePageSource.includes('자료 기준 자동 재검수'))
})

test('re-review asks for confirmation and names the paid call', () => {
  assert.ok(essencePageSource.includes('유료 AI 호출'))
  assert.ok(essencePageSource.includes('초안에 직접 고친 문장은 반영되지 않습니다'))
})

// M-03: 내용이 그대로면 서버는 재추출하지 않는다. 화면이 그때도 "완료"라고 하면 거짓 완료다.
test('reprocessing an unchanged source says nothing was left to do', () => {
  assert.ok(essencePageSource.includes('이미 최신 상태입니다'))
  assert.ok(essencePageSource.includes('processedAtBefore'))
})
