import assert from 'node:assert/strict'
import test from 'node:test'

import { legacyHospitalRedirect } from './route-redirects.ts'

const ID = 'h-1'

test('운영 요약과 운영 기준은 현황으로 돌아온다', () => {
  assert.equal(legacyHospitalRedirect(ID, 'dashboard'), '/hospitals/h-1')
  // 운영 기준의 예외 확인은 현황의 예외 카드가 이어받았다.
  assert.equal(legacyHospitalRedirect(ID, 'essence'), '/hospitals/h-1')
})

test('온보딩·병원 기본 정보·자료는 병원 정보 한 화면으로 모인다', () => {
  assert.equal(legacyHospitalRedirect(ID, 'onboarding'), '/hospitals/h-1/info')
  assert.equal(legacyHospitalRedirect(ID, 'profile'), '/hospitals/h-1/info')
  assert.equal(legacyHospitalRedirect(ID, 'wiki'), '/hospitals/h-1/info')
})

test('자기 도메인 링크만 병원 정보의 도메인 위치로 보낸다', () => {
  // 서버는 `#domain-setup`을 볼 수 없다 — `?section=domain`으로 온 링크만 앵커를 받는다.
  assert.equal(
    legacyHospitalRedirect(ID, 'profile', { section: 'domain' }),
    '/hospitals/h-1/info#domain-setup',
  )
  assert.equal(legacyHospitalRedirect(ID, 'profile', { section: 'facts' }), '/hospitals/h-1/info')
  // 앵커는 자기 도메인 링크만의 것이다 — 다른 옛 화면에는 붙지 않는다.
  assert.equal(legacyHospitalRedirect(ID, 'wiki', { section: 'domain' }), '/hospitals/h-1/info')
})

test('발행 일정은 콘텐츠 화면의 발행 요일 자리로 간다', () => {
  assert.equal(
    legacyHospitalRedirect(ID, 'schedule'),
    '/hospitals/h-1/content#content-schedule',
  )
})

test('환자 질문과 노출 보완은 콘텐츠 화면의 읽기 전용 신호로 간다', () => {
  assert.equal(
    legacyHospitalRedirect(ID, 'query-targets'),
    '/hospitals/h-1/content#content-signals',
  )
  assert.equal(
    legacyHospitalRedirect(ID, 'exposure-actions'),
    '/hospitals/h-1/content#content-signals',
  )
})

test('모르는 조각은 목적지가 없다 — 조용히 현황으로 보내지 않는다', () => {
  assert.equal(legacyHospitalRedirect(ID, 'reports'), null)
  assert.equal(legacyHospitalRedirect(ID, 'info'), null)
  assert.equal(legacyHospitalRedirect(ID, ''), null)
})

test('콘텐츠 딥링크의 content 파라미터는 앵커와 함께 살아남는다', () => {
  assert.equal(
    legacyHospitalRedirect(ID, 'schedule', { content: 'abc' }),
    '/hospitals/h-1/content?content=abc#content-schedule',
  )
  assert.equal(
    legacyHospitalRedirect(ID, 'exposure-actions', { content: 'abc', year: '2026', month: '9' }),
    '/hospitals/h-1/content?content=abc&year=2026&month=9#content-signals',
  )
})

test('다른 목적지에서도 알려진 파라미터만 남고 나머지는 버린다', () => {
  assert.equal(
    legacyHospitalRedirect(ID, 'onboarding', { leadId: 'l-9', step: 'profile', token: 'x' }),
    '/hospitals/h-1/info?leadId=l-9',
  )
  assert.equal(
    legacyHospitalRedirect(ID, 'dashboard', { tab: 'sov', content: 'c-1' }),
    '/hospitals/h-1?content=c-1',
  )
  // 옛 화면 전용 파라미터만 있으면 query 자체가 붙지 않는다.
  assert.equal(legacyHospitalRedirect(ID, 'dashboard', { tab: 'sov' }), '/hospitals/h-1')
})

test('같은 이름이 여러 번 온 파라미터는 첫 값만 쓴다', () => {
  assert.equal(
    legacyHospitalRedirect(ID, 'schedule', { content: ['a', 'b'] }),
    '/hospitals/h-1/content?content=a#content-schedule',
  )
  assert.equal(legacyHospitalRedirect(ID, 'dashboard', { content: [] }), '/hospitals/h-1')
})
