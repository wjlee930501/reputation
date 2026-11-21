import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')
const HEADER = read('../app/hospitals/[id]/layout.tsx')
const LIST = read('../app/hospitals/page.tsx')

test('계약 종료일은 머리글 ⋯ 메뉴에서 PUT 한 번으로 저장하고, 비우면 지운다', () => {
  assert.match(HEADER, /`\/admin\/hospitals\/\$\{hospitalId\}\/contract-end`/)
  assert.match(HEADER, /method: 'PUT'/)
  assert.match(HEADER, /ended_on: contractEndValue \|\| null/)
  assert.match(HEADER, /ADMIN_COPY\.contractEnd\.menuItem/)
  // 일시 정지 중인 병원(재개 단추만 있는 머리글)에서도 메뉴가 열린다.
  assert.match(HEADER, /\.\.\.\(hospital \? \[\{ label: ADMIN_COPY\.contractEnd\.menuItem/)
  // 「운영 종료」는 서버 판정만 읽는다 — 화면이 오늘 날짜와 비교하지 않는다.
  assert.match(HEADER, /hospital\.contract_ended \? ADMIN_COPY\.contractEnd\.endedBadge/)
  assert.doesNotMatch(HEADER, /new Date\(\)[\s\S]{0,80}contract_ended_on/)
})

test('목록은 운영 종료를 일시 정지와 다른 접힌 묶음으로 둔다', () => {
  assert.match(LIST, /isContractEnded\(h\)/)
  assert.match(LIST, /ADMIN_COPY\.contractEnd\.group/)
  assert.match(LIST, /data-testid=\{`\$\{group\.key\}-hospitals`\}/)
})
