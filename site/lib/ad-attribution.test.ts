import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ATTRIBUTION_COOKIE,
  attributionCookieDomain,
  attributionEventParams,
  buildAttributionCookie,
  decorateSourcePath,
  deserializeAttribution,
  parseAttribution,
  readAttributionCookie,
  serializeAttribution,
  type Attribution,
} from './ad-attribution.ts'

const AD_CLICK =
  '?utm_source=chatgpt&utm_medium=cpc&utm_campaign=reputation_chatgpt_2610&utm_content=ad-1&oai_campaign=c-1&oai_adgroup=g-1&oppref=abc123'

test('parseAttribution captures the ad click parameters with the landing path', () => {
  const attribution = parseAttribution(AD_CLICK, '/')
  assert.deepEqual(attribution, {
    utm_source: 'chatgpt',
    utm_medium: 'cpc',
    utm_campaign: 'reputation_chatgpt_2610',
    oppref: 'abc123',
    utm_content: 'ad-1',
    oai_campaign: 'c-1',
    oai_adgroup: 'g-1',
    landing_path: '/',
  })
})

test('parseAttribution accepts a search string without the leading question mark', () => {
  assert.equal(parseAttribution('utm_source=chatgpt', '/ai-diagnosis')?.utm_source, 'chatgpt')
})

test('parseAttribution returns null for organic visits', () => {
  assert.equal(parseAttribution('', '/'), null)
  assert.equal(parseAttribution('?ref=blog', '/'), null)
  // 빈 값만 있는 경우도 광고 유입으로 취급하지 않는다.
  assert.equal(parseAttribution('?utm_source=&utm_medium=', '/'), null)
})

test('parseAttribution rejects a landing path that is not a path', () => {
  assert.equal(parseAttribution('?utm_source=chatgpt', 'https://evil.example')?.landing_path, '/')
})

test('parseAttribution strips control characters and caps value length', () => {
  const attribution = parseAttribution('?utm_source=chat\u0000gpt', '/')
  assert.equal(attribution?.utm_source, 'chatgpt')

  const long = 'x'.repeat(500)
  assert.equal(parseAttribution(`?utm_campaign=${long}`, '/')?.utm_campaign?.length, 200)
})

test('serialize and deserialize round-trip', () => {
  const attribution = parseAttribution(AD_CLICK, '/') as Attribution
  assert.deepEqual(deserializeAttribution(serializeAttribution(attribution)), attribution)
})

test('deserializeAttribution rejects junk instead of throwing', () => {
  assert.equal(deserializeAttribution(''), null)
  assert.equal(deserializeAttribution('not-json'), null)
  assert.equal(deserializeAttribution(encodeURIComponent('[1,2]')), null)
  assert.equal(deserializeAttribution(encodeURIComponent('{"landing_path":"/"}')), null)
})

test('readAttributionCookie finds the entry among other cookies', () => {
  const attribution = parseAttribution('?utm_source=chatgpt', '/') as Attribution
  const cookieString = `_ga=GA1.1.x; ${ATTRIBUTION_COOKIE}=${serializeAttribution(attribution)}; _clck=y`
  assert.deepEqual(readAttributionCookie(cookieString), attribution)
})

test('readAttributionCookie returns null when the cookie is absent', () => {
  assert.equal(readAttributionCookie('_ga=GA1.1.x'), null)
  assert.equal(readAttributionCookie(''), null)
})

test('attributionCookieDomain only claims the motionlabs registrable domain', () => {
  assert.equal(attributionCookieDomain('reputation.motionlabs.kr'), '.motionlabs.kr')
  assert.equal(attributionCookieDomain('motionlabs.kr'), '.motionlabs.kr')
  assert.equal(attributionCookieDomain('MOTIONLABS.KR'), '.motionlabs.kr')
  // 병원 커스텀 도메인에 남의 도메인 쿠키를 쓰면 브라우저가 통째로 거부한다.
  assert.equal(attributionCookieDomain('jang-clinic.co.kr'), null)
  // 접미사만 흉내 낸 도메인도 우리 것이 아니다.
  assert.equal(attributionCookieDomain('notmotionlabs.kr'), null)
  assert.equal(attributionCookieDomain('localhost'), null)
})

test('buildAttributionCookie sets a 30-day first-party cookie', () => {
  const attribution = parseAttribution('?utm_source=chatgpt', '/') as Attribution
  const cookie = buildAttributionCookie(attribution, 'reputation.motionlabs.kr')
  assert.match(cookie, /^reputation_ad_attribution=/)
  assert.match(cookie, /max-age=2592000/)
  assert.match(cookie, /domain=\.motionlabs\.kr/)
  assert.match(cookie, /SameSite=Lax/)
  assert.match(cookie, /Secure/)
})

test('buildAttributionCookie drops Secure and domain on localhost', () => {
  const attribution = parseAttribution('?utm_source=chatgpt', '/') as Attribution
  const cookie = buildAttributionCookie(attribution, 'localhost')
  assert.doesNotMatch(cookie, /Secure/)
  assert.doesNotMatch(cookie, /domain=/)
})

test('decorateSourcePath carries the capture into the lead record', () => {
  const attribution = parseAttribution('?utm_source=chatgpt&utm_medium=cpc&oppref=abc123', '/') as Attribution
  const decorated = decorateSourcePath('/ai-diagnosis', attribution)
  const url = new URL(decorated, 'https://reputation.motionlabs.kr')
  assert.equal(url.pathname, '/ai-diagnosis')
  assert.equal(url.searchParams.get('utm_source'), 'chatgpt')
  assert.equal(url.searchParams.get('utm_medium'), 'cpc')
  assert.equal(url.searchParams.get('oppref'), 'abc123')
  assert.equal(url.searchParams.get('landing_path'), '/')
})

test('decorateSourcePath leaves organic submissions untouched', () => {
  assert.equal(decorateSourcePath('/#contact', null), '/#contact')
})

test('decorateSourcePath never exceeds the backend source_path column', () => {
  const long = 'x'.repeat(200)
  const attribution = parseAttribution(
    `?utm_source=${long}&utm_medium=${long}&utm_campaign=${long}&oppref=${long}`,
    '/',
  ) as Attribution
  const decorated = decorateSourcePath('/ai-diagnosis', attribution)
  assert.ok(decorated.length <= 500, `source_path was ${decorated.length} chars`)
  // 앞쪽 파라미터(가장 중요한 것)는 살아남는다.
  assert.match(decorated, /utm_source=/)
})

test('attributionEventParams mirrors the capture for GA4 events', () => {
  const attribution = parseAttribution('?utm_source=chatgpt&oppref=abc123', '/') as Attribution
  assert.deepEqual(attributionEventParams(attribution), {
    utm_source: 'chatgpt',
    oppref: 'abc123',
    landing_path: '/',
  })
  assert.deepEqual(attributionEventParams(null), {})
})
