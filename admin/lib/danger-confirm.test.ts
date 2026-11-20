import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { typedConfirmationMatches } from './danger-confirm.ts'

test('typed confirmation must match the name exactly, ignoring outer spaces only', () => {
  assert.equal(typedConfirmationMatches('행복드림의원', ' 행복드림의원 '), true)
  assert.equal(typedConfirmationMatches('행복드림의원', '행복드림'), false)
  assert.equal(typedConfirmationMatches('행복드림의원', '행복드림 의원'), false)
  assert.equal(typedConfirmationMatches('Ops@Example.com', 'ops@example.com'), false)
  assert.equal(typedConfirmationMatches(null, ''), true)
})

// 위험 동작은 ⋯ 메뉴 안 + 확인창. 확인 없이 바로 실행되던 계정 정지·비용 전체 중지까지.
test('dangerous actions go through the shared confirm dialog', () => {
  const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')
  const header = read('../app/hospitals/[id]/layout.tsx')
  const leads = read('../app/leads/page.tsx')
  const accounts = read('../app/accounts/page.tsx')
  const costGuard = read('../app/operations/CostGuardPanel.tsx')
  const content = read('../app/hospitals/[id]/content/page.tsx')
  const menu = read('../app/_components/DangerMenu.tsx')
  assert.match(menu, /min-h-11/)
  for (const [name, source] of [['header', header], ['leads', leads], ['accounts', accounts], ['costGuard', costGuard]] as const) {
    assert.match(source, /ConfirmDialog/, name)
    assert.doesNotMatch(source, /window\.confirm|[^.\w]confirm\(/, name)
  }
  for (const [name, source] of [['header', header], ['leads', leads], ['accounts', accounts], ['content', content]] as const) {
    assert.match(source, /DangerMenu/, name)
  }
  assert.match(header, /typedConfirmation=\{[^}]*hospital\?\.name/)
  assert.match(leads, /typedConfirmation=/)
  assert.match(accounts, /typedConfirmation=/)
  assert.match(costGuard, /typedConfirmation=/)
})
