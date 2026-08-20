import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const ONBOARDING_PAGE = readFileSync(
  new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url),
  'utf8',
)

test('initial hospital loading renders only the loading phrase before lifecycle derivation', () => {
  const loadingBranch = ONBOARDING_PAGE.match(
    /if \(loading && !hospital\) \{([\s\S]*?)\n  \}/,
  )?.[1]

  assert.equal(loadingBranch?.trim(), 'return <p>온보딩 정보를 불러오는 중…</p>')
  assert.ok(
    ONBOARDING_PAGE.indexOf('const steps = deriveOnboardingSteps(')
      > ONBOARDING_PAGE.indexOf('if (loading && !hospital)'),
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
