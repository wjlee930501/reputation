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

test('rate limiting is an error state with Korean scope and a retry action', () => {
  const layout = readFileSync(
    new URL('../app/hospitals/[id]/layout.tsx', import.meta.url),
    'utf8',
  )

  assert.match(layout, /요청이 잠시 제한되었습니다/)
  assert.match(layout, /병원 정보 확인 필요/)
  assert.match(layout, /onClick=\{\(\) => void refetch\(\)\}/)
  assert.match(layout, /다시 시도/)
  assert.doesNotMatch(layout, /Too many requests/)
})

test('mobile exposure refresh keeps its Hangul label on one line', () => {
  const page = readFileSync(
    new URL('../app/hospitals/[id]/exposure-actions/page.tsx', import.meta.url),
    'utf8',
  )

  assert.match(page, /min-w-fit/)
  assert.match(page, /whitespace-nowrap/)
  assert.match(page, />\s*새로고침\s*</)
})

test('manual essence action is explicitly an exception to automatic approval', () => {
  const page = readFileSync(
    new URL('../app/hospitals/[id]/essence/page.tsx', import.meta.url),
    'utf8',
  )

  assert.match(page, /정상 경로는 AI 시스템 자동 승인입니다/)
  assert.match(page, /자동 보류 예외 승인/)
  assert.doesNotMatch(page, /사람 승인 아님/)
})
