// 현황 화면의 소스 계약.
//
// 세 상태·예외·이번 달 요약은 헤더가 이미 받아 둔 overview 한 응답에서만 온다. 화면이
// 조각마다 다시 부르면 카드마다 다른 순간의 병원을 보여 주고, 옛 운영 화면의 실행
// 도구가 병원 화면으로 되돌아온다.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

function read(relative: string): string {
  return readFileSync(new URL(relative, import.meta.url), 'utf8')
}

const page = read('../app/hospitals/[id]/page.tsx')
const statusCards = read('../app/hospitals/[id]/status/StatusCards.tsx')
const exceptionCards = read('../app/hospitals/[id]/status/ExceptionCards.tsx')
const escalatedDraft = read('../app/hospitals/[id]/status/EscalatedDraftCard.tsx')
const monthSummary = read('../app/hospitals/[id]/status/MonthSummary.tsx')
const all = [page, statusCards, exceptionCards, escalatedDraft, monthSummary].join('\n')

test('현황은 헤더가 받아 둔 overview만 읽는다', () => {
  assert.match(page, /useHospitalHeader/)
  // 상태·요약을 그리는 쪽은 서버를 다시 부르지 않는다. 요청은 예외 카드의 실행과
  // 예외 초안 편집에만 있다.
  for (const [name, source] of [
    ['page.tsx', page],
    ['StatusCards.tsx', statusCards],
    ['MonthSummary.tsx', monthSummary],
  ] as const) {
    assert.doesNotMatch(source, /fetchAPI\(/, `${name}이 직접 조회한다`)
  }
  // 헤더가 이미 부른 것과 옛 운영 요약 화면이 부르던 것을 다시 부르지 않는다.
  for (const path of ['/overview', '/readiness', '/sov', '/usage', '/audit']) {
    assert.equal(all.includes(path), false, `현황 화면이 ${path}를 직접 부른다`)
  }
})

test('옛 운영 화면의 실행 도구와 분석 표는 현황에 없다', () => {
  for (const banned of ['다시 실행', '측정 실행 로그', '운영 흐름', '노출 개선 우선순위', '질문별', '사용량']) {
    assert.equal(all.includes(banned), false, `현황 화면에 "${banned}"이(가) 남아 있다`)
  }
})

test('현황은 판정·버튼·요약을 공용 함수에서 받는다', () => {
  assert.match(statusCards, /splitRemaining/)
  assert.match(statusCards, /stateCardCopy/)
  assert.match(exceptionCards, /cardButtons/)
  assert.match(exceptionCards, /mutationBodyFor/)
  assert.match(monthSummary, /monthSummaryLines/)
  assert.match(page, /<StatusCards/)
  assert.match(page, /<ExceptionCards/)
  assert.match(page, /<MonthSummary/)
  assert.match(exceptionCards, /EscalatedDraftCard/)
})

test('인시던트 행동은 운영 센터와 같은 배관을 쓴다', () => {
  // 결과를 모른 채 같은 변경을 두 번 사지 않는다.
  assert.match(exceptionCards, /getOrCreatePendingActionKey/)
  assert.match(exceptionCards, /requires_idempotency_key/)
  assert.match(exceptionCards, /'Idempotency-Key'/)
  // 충돌·권한 응답은 운영 센터와 같은 문구로 말한다.
  assert.match(exceptionCards, /interpretOperationsConflict/)
  assert.match(exceptionCards, /status === 403/)
  assert.match(exceptionCards, /권한 있는 담당자만/)
  // 담당 후보는 서버가 준 목록에서만 고른다.
  assert.match(exceptionCards, /assignable_accounts/)
  // 카드는 서버가 준 경로·메서드를 그대로 쓴다.
  assert.match(exceptionCards, /fetchAPI\(action\.path/)
  assert.match(exceptionCards, /method: action\.method/)
})

test('예외 초안 카드는 편집·재검수·예외 승인 세 경로를 모두 가진다', () => {
  assert.match(escalatedDraft, /\/essence\/philosophies/)
  assert.match(escalatedDraft, /\/philosophy\//)
  assert.match(escalatedDraft, /\/re-review/)
  assert.match(escalatedDraft, /\/approve/)
  assert.match(escalatedDraft, /method: 'PATCH'/)
  assert.match(escalatedDraft, /RE_REVIEW_CONFIRM/)
  assert.match(escalatedDraft, /reReviewNotice/)
  assert.match(escalatedDraft, /reReviewErrorMessage/)
  assert.match(escalatedDraft, /approveErrorMessage/)
  // 검토자는 로그인 계정이다 — 화면이 고른 이름으로 승인 기록을 남기지 않는다.
  assert.match(escalatedDraft, /fetchCurrentAccount/)
  assert.match(escalatedDraft, /근거 노트와 원문 발췌를 검토했습니다\./)
})

test('예외가 없을 때와 현황을 못 받았을 때 할 말이 있다', () => {
  assert.match(exceptionCards, /지금 사람이 결정할 항목이 없습니다\./)
  assert.match(page, /현황을 불러오지 못했습니다/)
  assert.match(page, /refetch\(\)/)
})

test('이번 달 요약은 콘텐츠·보고서 화면으로만 이어진다', () => {
  assert.match(monthSummary, /\/hospitals\/\$\{hospitalId\}\/content/)
  assert.match(monthSummary, /\/hospitals\/\$\{hospitalId\}\/reports/)
})
