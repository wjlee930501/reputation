import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_GA_MEASUREMENT_ID,
  buildEventParams,
  configureGa,
  markGaConfigured,
  pendingEventsForTest,
  resetAnalyticsStateForTest,
  resolveGaPlan,
  trackEvent,
} from './analytics.ts'

test('platform hosts share the motionlabs.kr property and cookie', () => {
  assert.deepEqual(resolveGaPlan('reputation.motionlabs.kr', undefined), {
    measurementId: DEFAULT_GA_MEASUREMENT_ID,
    cookieDomain: '.motionlabs.kr',
  })
  assert.deepEqual(resolveGaPlan('motionlabs.kr', undefined), {
    measurementId: DEFAULT_GA_MEASUREMENT_ID,
    cookieDomain: '.motionlabs.kr',
  })
})

test('an explicit measurement id overrides the default on platform hosts', () => {
  assert.deepEqual(resolveGaPlan('reputation.motionlabs.kr', 'G-OTHER123'), {
    measurementId: 'G-OTHER123',
    cookieDomain: '.motionlabs.kr',
  })
})

test('hospital custom domains stay untracked unless the operator opts in', () => {
  // 기본값으로 켜면 병원 자체 도메인 방문자가 묻지도 않고 우리 속성에 쌓인다.
  assert.equal(resolveGaPlan('jang-clinic.co.kr', undefined), null)
  assert.equal(resolveGaPlan('localhost', undefined), null)
  assert.deepEqual(resolveGaPlan('jang-clinic.co.kr', 'G-TENANT1'), {
    measurementId: 'G-TENANT1',
    // 남의 도메인에 .motionlabs.kr 쿠키를 쓰면 저장 자체가 실패한다.
    cookieDomain: 'auto',
  })
})

test('a malformed measurement id disables tracking rather than guessing', () => {
  assert.equal(resolveGaPlan('reputation.motionlabs.kr', 'UA-12345-1'), null)
  assert.equal(resolveGaPlan('reputation.motionlabs.kr', 'g-lowercase'), null)
})

test('an empty or whitespace env value falls back to the default on platform hosts', () => {
  assert.equal(resolveGaPlan('reputation.motionlabs.kr', '   ')?.measurementId, DEFAULT_GA_MEASUREMENT_ID)
})

test('a lookalike domain is not a platform host', () => {
  assert.equal(resolveGaPlan('notmotionlabs.kr', undefined), null)
})

test('every event carries the service tag and drops empty values', () => {
  assert.deepEqual(buildEventParams({}), { service: 'reputation' })
  assert.deepEqual(buildEventParams({ utm_source: 'chatgpt', utm_medium: undefined, oppref: '' }), {
    service: 'reputation',
    utm_source: 'chatgpt',
  })
})

test('events fired before gtag is configured are queued, not lost', () => {
  resetAnalyticsStateForTest()
  // 폼 이펙트는 GA 스크립트보다 먼저 돈다 — 이때 버려지면 lead_form_view가 통째로 사라진다.
  trackEvent('lead_form_view', { utm_source: 'chatgpt' })
  trackEvent('lead_form_start')
  assert.deepEqual(
    pendingEventsForTest().map((event) => event.name),
    ['lead_form_view', 'lead_form_start'],
  )
  assert.deepEqual(pendingEventsForTest()[0].params, {
    service: 'reputation',
    utm_source: 'chatgpt',
  })
  resetAnalyticsStateForTest()
})

test('the queue drains once gtag is configured and stays bounded', () => {
  resetAnalyticsStateForTest()
  for (let index = 0; index < 50; index += 1) trackEvent(`event_${index}`)
  assert.equal(pendingEventsForTest().length, 20)
  // gtag가 없는 환경이라 실제 전송은 없지만, 큐는 비워진다.
  markGaConfigured()
  assert.equal(pendingEventsForTest().length, 0)
  resetAnalyticsStateForTest()
})
