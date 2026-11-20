import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// 2026-11-20 마케팅 요청: 제외 자료는 기본으로 접고, 목록은 처음 5개만, 제외 표시한 노트도 되돌릴 수 있게.
test('근거 자료 목록은 제외 자료를 접고 제외 노트를 되돌릴 수 있게 둔다', () => {
  const page = readFileSync(new URL('../app/hospitals/[id]/info/SourcesSection.tsx', import.meta.url), 'utf8')
  assert.match(page, /status !== 'EXCLUDED'/)
  assert.match(page, /data-testid="excluded-sources"/)
  assert.match(page, /SOURCE_PREVIEW_COUNT = 5/)
  assert.match(page, /\?include_noise=true/)
  // 복원은 기존 reinclude API — 영구 삭제는 결정 대기(5번)라 없다.
  assert.match(page, /\/reinclude/)
  assert.doesNotMatch(page, /method: 'DELETE'/)
})
