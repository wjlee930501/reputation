import assert from 'node:assert/strict'
import test from 'node:test'

import { aiTrafficEvent, classifyAiReferral, classifyCrawler } from './ai-traffic.ts'

test('classifyCrawler names search and AI crawlers, including Korean portals', () => {
  assert.equal(
    classifyCrawler('Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot'),
    'OAI-SearchBot',
  )
  assert.equal(classifyCrawler('Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)'), 'Googlebot')
  assert.equal(classifyCrawler('Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)'), 'Bingbot')
  assert.equal(classifyCrawler('Mozilla/5.0 (compatible; Yeti/1.1; +https://naver.me/spd)'), 'Yeti')
  assert.equal(classifyCrawler('Mozilla/5.0 (compatible; Daumoa/4.0)'), 'Daumoa')
  assert.equal(classifyCrawler('Mozilla/5.0 (Macintosh) Safari/605.1.15'), null)
  assert.equal(classifyCrawler(null), null)
})

test('classifyAiReferral reads the referrer host first, then utm_source', () => {
  assert.equal(classifyAiReferral('https://chatgpt.com/', null), 'chatgpt')
  assert.equal(classifyAiReferral('https://gemini.google.com/app', null), 'gemini')
  assert.equal(classifyAiReferral('https://www.perplexity.ai/search', null), 'perplexity')
  assert.equal(classifyAiReferral(null, 'chatgpt.com'), 'chatgpt')
  assert.equal(classifyAiReferral('https://www.google.com/', null), null)
  assert.equal(classifyAiReferral('https://notchatgpt.com/', null), null)
  assert.equal(classifyAiReferral('::bad::', 'newsletter'), null)
})

test('aiTrafficEvent keeps only host and a bounded path, never the query string', () => {
  const crawl = aiTrafficEvent({
    host: 'clinic.example.com:443',
    pathname: '/contents/abc',
    userAgent: 'OAI-SearchBot/1.0',
    referer: null,
    utmSource: null,
  })
  assert.deepEqual(crawl, {
    event: 'ai_crawler_hit',
    severity: 'INFO',
    message: 'ai_crawler_hit OAI-SearchBot',
    host: 'clinic.example.com',
    path: '/contents/abc',
    crawler: 'OAI-SearchBot',
  })
  const visit = aiTrafficEvent({
    host: 'clinic.example.com',
    pathname: '/x'.repeat(300),
    userAgent: 'Mozilla/5.0',
    referer: 'https://chatgpt.com/',
    utmSource: null,
  })
  assert.equal(visit?.event, 'ai_referral_visit')
  assert.equal(visit?.source, 'chatgpt')
  assert.equal(visit?.path.length, 200)
  assert.equal(
    aiTrafficEvent({ host: 'a.example', pathname: '/', userAgent: 'Mozilla', referer: null, utmSource: null }),
    null,
  )
})
