import assert from 'node:assert/strict'
import test from 'node:test'

import {
  OPENAI_PIXEL_ID,
  leadEventId,
  markPixelInitializedForTest,
  pageViewedContent,
  pendingPixelEventsForTest,
  resetPixelStateForTest,
  resolvePixelId,
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

test('the lead event id is derived from the stored record', () => {
  assert.equal(leadEventId('abc-123'), 'diagnosis_abc-123')
})

test('events fired before init are queued in order, not lost', () => {
  resetPixelStateForTest()
  trackPixelPageViewed('/')
  trackPixelLeadCreated('abc-123')
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
  trackPixelLeadCreated(null)
  trackPixelLeadCreated(undefined)
  trackPixelLeadCreated('')
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
