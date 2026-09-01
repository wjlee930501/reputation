import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const ONBOARDING_PAGE = readFileSync(
  new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
  'utf8',
)

test('initial hospital loading renders only the loading phrase before lifecycle derivation', () => {
  // hospital은 이제 레이아웃 헤더 컨텍스트(useHospitalHeader)에서 온다 — 이 페이지
  // 자신의 refresh() 로딩(loading)과 컨텍스트 로딩(headerLoading)을 둘 다 확인해야
  // "아직 못 받아옴"과 "실패/없음"을 구분할 수 있다.
  const loadingBranch = ONBOARDING_PAGE.match(
    /if \(\(loading \|\| headerLoading\) && !hospital\) \{([\s\S]*?)\n  \}/,
  )?.[1]

  assert.equal(loadingBranch?.trim(), 'return <p>온보딩 정보를 불러오는 중…</p>')
  assert.ok(
    ONBOARDING_PAGE.indexOf('const steps = deriveOnboardingSteps(')
      > ONBOARDING_PAGE.indexOf('if ((loading || headerLoading) && !hospital)'),
  )
  assert.ok(
    ONBOARDING_PAGE.indexOf('const summary = deriveOnboardingSummary(')
      > ONBOARDING_PAGE.indexOf('if (!hospital)'),
  )
})

test('processing stays open while other cards still follow current state and every card has a caret', () => {
  assert.match(
    ONBOARDING_PAGE,
    /open=\{step\.key === 'processing' \|\| step\.status === 'current'\}/,
  )
  assert.doesNotMatch(ONBOARDING_PAGE, /open=\{true\}/)
  assert.equal([...ONBOARDING_PAGE.matchAll(/open=\{/g)].length, 1)
  assert.match(
    ONBOARDING_PAGE,
    /<summary[\s\S]*?<span aria-hidden className="[^"]*group-open:rotate-180">▼<\/span>[\s\S]*?<\/summary>/,
  )
})

test('accepted handoff hides only the due-date row and keeps accepted status', () => {
  assert.match(
    ONBOARDING_PAGE,
    /handoff\?\.state !== 'HANDOFF_ACCEPTED' && \([\s\S]*?<dt>인수 처리 기한<\/dt>/,
  )
  assert.match(ONBOARDING_PAGE, /<dt>처리 상태<\/dt>[\s\S]*handoffDueStatus\.label/)
})
