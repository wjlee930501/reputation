import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

test('hospital list becomes key-value cards at 768px', () => {
  const page = readFileSync(new URL('../app/hospitals/page.tsx', import.meta.url), 'utf8')
  const globals = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8')

  assert.match(page, /admin-responsive-table-wrap/)
  assert.match(page, /admin-responsive-table/)
  assert.match(globals, /@media \(max-width: 768px\)[\s\S]*?\.admin-responsive-table-wrap/)
})

test('narrow tables hide headers only visually so screen readers keep column names', () => {
  const globals = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8')
  const thead = globals.match(/\.admin-responsive-table thead \{([^}]*)\}/)
  assert.ok(thead, 'responsive table header rule')
  assert.doesNotMatch(thead[1], /display:\s*none/)
  assert.match(thead[1], /clip-path:\s*inset\(50%\)/)
  // 칸 앞 열 이름은 눈으로만 — 대체 텍스트를 비워 머리글과 두 번 읽히지 않게(옛 브라우저용 줄도 남긴다).
  assert.match(globals, /content: attr\(data-label\);\s*(?:\/\*[\s\S]*?\*\/\s*)?content: attr\(data-label\) \/ '';/)
})

test('rate limiting is an error state with Korean scope and a retry action', () => {
  const layout = readFileSync(
    new URL('../app/hospitals/[id]/layout.tsx', import.meta.url),
    'utf8',
  )

  assert.match(layout, /요청이 잠시 제한되었습니다/)
  assert.match(layout, /병원 정보 확인 필요/)
  assert.match(layout, /onClick=\{\(\) => void refetch\(\)\}/)
  assert.match(layout, /다시 불러오기/)
  assert.doesNotMatch(layout, /Too many requests/)
})
