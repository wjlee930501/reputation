import assert from 'node:assert/strict'
import test from 'node:test'

import {
  OPENAI_PIXEL_ID,
  initPixel,
  leadEventId,
  markPixelInitializedForTest,
  pageViewedContent,
  pendingPixelEventsForTest,
  resetPixelStateForTest,
  resolvePixelId,
  storedRecordId,
  trackPixelEvent,
  trackPixelLeadCreated,
  trackPixelPageViewed,
} from './openai-pixel.ts'

test('platform hosts use the issued pixel id', () => {
  assert.equal(resolvePixelId('reputation.motionlabs.kr', undefined), OPENAI_PIXEL_ID)
  assert.equal(resolvePixelId('motionlabs.kr', undefined), OPENAI_PIXEL_ID)
  assert.equal(resolvePixelId('MOTIONLABS.KR', undefined), OPENAI_PIXEL_ID)
})

test('hospital custom domains stay off unless the operator opts in', () => {
  // 그 방문자는 우리 광고의 전환 후보가 아니다.
  assert.equal(resolvePixelId('jang-clinic.co.kr', undefined), null)
  assert.equal(resolvePixelId('localhost', undefined), null)
  assert.equal(resolvePixelId('notmotionlabs.kr', undefined), null)
  assert.equal(resolvePixelId('localhost', 'StagingPixel123'), 'StagingPixel123')
})

test('a malformed pixel id disables the pixel rather than guessing', () => {
  assert.equal(resolvePixelId('reputation.motionlabs.kr', 'short'), null)
  assert.equal(resolvePixelId('reputation.motionlabs.kr', 'has spaces in it'), null)
  // 빈 값은 기본 픽셀로 돌아간다.
  assert.equal(resolvePixelId('reputation.motionlabs.kr', '   '), OPENAI_PIXEL_ID)
})

test('page_viewed describes only the two surfaces the brief defines', () => {
  assert.deepEqual(pageViewedContent('/'), {
    id: 'reputation_landing',
    name: 'Re:putation 랜딩',
    content_type: 'page',
  })
  assert.deepEqual(pageViewedContent('/ai-diagnosis'), {
    id: 'ai_diagnosis_form',
    name: 'AI 노출 진단 신청',
    content_type: 'page',
  })
  // 트레일링 슬래시는 같은 지면이다.
  assert.equal(pageViewedContent('/ai-diagnosis/')?.id, 'ai_diagnosis_form')
  // 병원 콘텐츠 페이지는 광고 지면이 아니다.
  assert.equal(pageViewedContent('/jang-clinic'), null)
  assert.equal(pageViewedContent('/ai-diagnosis/status/token'), null)
  assert.equal(pageViewedContent('/privacy'), null)
})

test('the lead event id is derived from the stored record, per intake path', () => {
  // 무료 진단의 형식은 첫 배포 그대로 유지한다 — 이미 쌓인 전환과 이어져야 한다.
  assert.equal(leadEventId('diagnosis', 'abc-123'), 'diagnosis_abc-123')
  assert.equal(leadEventId('inquiry', 'abc-123'), 'lead_abc-123')
})

test('only a stored record id counts as an accepted submission', () => {
  // 정상 접수
  assert.equal(storedRecordId('inquiry', { ok: true, lead_id: 'L-1', diagnosis_id: 'D-1' }), 'L-1')
  assert.equal(storedRecordId('diagnosis', { ok: true, diagnosis_id: 'D-1' }), 'D-1')
  // 백엔드 허니팟: 200 + null
  assert.equal(storedRecordId('inquiry', { ok: true, lead_id: null, created_at: null }), null)
  assert.equal(storedRecordId('diagnosis', { ok: true, diagnosis_id: null }), null)
  // Site BFF 허니팟: 200 + ID 없음
  assert.equal(storedRecordId('inquiry', { ok: true }), null)
  // 도입문의의 diagnosis_id는 부수효과라 리드 저장 여부의 신호가 아니다.
  assert.equal(storedRecordId('inquiry', { ok: true, diagnosis_id: 'D-1' }), null)
  // 본문 파싱 실패·이상한 값
  assert.equal(storedRecordId('inquiry', {}), null)
  assert.equal(storedRecordId('inquiry', null), null)
  assert.equal(storedRecordId('inquiry', { lead_id: '  ' }), null)
  assert.equal(storedRecordId('inquiry', { lead_id: 42 }), null)
})

test('events fired before init are queued in order, not lost', () => {
  resetPixelStateForTest()
  trackPixelPageViewed('/')
  trackPixelLeadCreated('diagnosis', 'abc-123')
  assert.deepEqual(
    pendingPixelEventsForTest().map((event) => event.name),
    ['page_viewed', 'lead_created'],
  )
  assert.deepEqual(pendingPixelEventsForTest()[1].options, { event_id: 'diagnosis_abc-123' })
  resetPixelStateForTest()
})

test('a rejected or bot submission never becomes a conversion', () => {
  resetPixelStateForTest()
  // 백엔드는 허니팟에 걸린 요청에도 200을 주되 diagnosis_id를 null로 돌려준다.
  trackPixelLeadCreated('inquiry', null)
  trackPixelLeadCreated('inquiry', undefined)
  trackPixelLeadCreated('diagnosis', '')
  assert.equal(pendingPixelEventsForTest().length, 0)
  resetPixelStateForTest()
})

test('the queue stays bounded when the pixel is off', () => {
  resetPixelStateForTest()
  for (let index = 0; index < 50; index += 1) trackPixelEvent(`event_${index}`, {})
  assert.equal(pendingPixelEventsForTest().length, 20)
  resetPixelStateForTest()
})

test('after init, events no longer queue', () => {
  resetPixelStateForTest()
  markPixelInitializedForTest()
  // oaiq가 없는 환경이라 전송은 없지만, 큐에 쌓이지도 않는다.
  trackPixelPageViewed('/')
  assert.equal(pendingPixelEventsForTest().length, 0)
  resetPixelStateForTest()
})

test('init goes out first, then queued events flush in order without debug', () => {
  resetPixelStateForTest()
  const calls: unknown[][] = []
  const globals = globalThis as unknown as { window?: unknown }
  const previous = globals.window
  globals.window = {
    oaiq: (...args: unknown[]) => {
      calls.push(args)
    },
  }
  try {
    trackPixelPageViewed('/')
    trackPixelLeadCreated('inquiry', 'L-1')
    assert.equal(calls.length, 0, 'init 전에는 아무것도 나가지 않는다')

    initPixel(OPENAI_PIXEL_ID)
    assert.deepEqual(calls[0], ['init', { pixelId: OPENAI_PIXEL_ID }])
    assert.deepEqual(
      calls.slice(1).map((args) => [args[0], args[1]]),
      [
        ['measure', 'page_viewed'],
        ['measure', 'lead_created'],
      ],
    )
    assert.deepEqual(calls[2][3], { event_id: 'lead_L-1' })
    assert.equal(pendingPixelEventsForTest().length, 0)

    // SPA 재마운트가 init을 다시 부르지 않는다.
    initPixel(OPENAI_PIXEL_ID)
    assert.equal(calls.filter((args) => args[0] === 'init').length, 1)
  } finally {
    globals.window = previous
    resetPixelStateForTest()
  }
})
