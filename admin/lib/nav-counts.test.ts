import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { navBadge } from './nav-counts.ts'

const counts = { operator_todo_groups: 3, hospital_count: 18, lead_total: 17, lead_needs_attention: 2 }

test('the operations badge is the to-do count in orange; others are plain counts', () => {
  assert.deepEqual(navBadge('operations', counts), { value: '3', label: '처리할 일 3건', tone: 'attention' })
  assert.deepEqual(navBadge('hospitals', counts), { value: '18', label: '18건', tone: 'count' })
  assert.equal(navBadge('leads', counts)?.value, '17')
})

test('zero, missing or broken counts hide the badge', () => {
  assert.equal(navBadge('operations', { ...counts, operator_todo_groups: 0 }), null)
  assert.equal(navBadge('operations', null), null)
  assert.equal(navBadge('hospitals', { ...counts, hospital_count: Number.NaN }), null)
  assert.equal(navBadge('operations', { ...counts, operator_todo_groups: 120 })?.value, '99+')
})

// 메뉴 이름은 바꾸되 경로는 그대로 — 백엔드 딥링크(`incident_safety._ADMIN_ROUTE_ROOTS`)가 이 경로를 쓴다.
test('the shell keeps every route while renaming the menu', () => {
  const shell = readFileSync(new URL('../app/AdminShell.tsx', import.meta.url), 'utf8')
  for (const href of ['/hospitals', '/operations', '/leads', '/diagnoses', '/hospitals/new', '/accounts']) {
    assert.match(shell, new RegExp(`href: '${href}'`))
  }
  assert.match(shell, /\/admin\/operations\/nav-counts/)
  assert.doesNotMatch(shell, /운영 센터|병원 목록|운영자 계정|노출 진단 생성/)
})
