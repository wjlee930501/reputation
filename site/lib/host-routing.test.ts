import assert from 'node:assert/strict'
import test from 'node:test'

import {
  decideCanonicalRedirect,
  decideRewrite,
  getPrimaryHostnames,
  isPrimaryHost,
  isReservedPath,
  normalizeHostname,
  resolveRequestHost,
  shouldFailClosedCustomHost,
} from './host-routing.ts'

const PRIMARY = getPrimaryHostnames('https://reputation.motionlabs.kr')

test('normalizeHostname strips port, lowercases, and handles IPv6', () => {
  assert.equal(normalizeHostname('Clinic.Example.com'), 'clinic.example.com')
  assert.equal(normalizeHostname('localhost:3000'), 'localhost')
  assert.equal(normalizeHostname('[::1]:3000'), '::1')
  assert.equal(normalizeHostname(''), null)
  assert.equal(normalizeHostname(null), null)
})

test('getPrimaryHostnames includes site host and local hosts, tolerates bad URL', () => {
  assert.ok(PRIMARY.includes('reputation.motionlabs.kr'))
  assert.ok(PRIMARY.includes('localhost'))
  assert.ok(PRIMARY.includes('127.0.0.1'))
  // 잘못된 SITE_URL이어도 throw 없이 로컬 호스트만 반환
  assert.ok(getPrimaryHostnames('not a url').includes('localhost'))
  assert.ok(getPrimaryHostnames(undefined).includes('localhost'))
})

test('isPrimaryHost: platform, localhost with port, 127.0.0.1, run.app, vercel.app pass through', () => {
  assert.equal(isPrimaryHost('reputation.motionlabs.kr', PRIMARY), true)
  assert.equal(isPrimaryHost('localhost:3000', PRIMARY), true)
  assert.equal(isPrimaryHost('127.0.0.1:3000', PRIMARY), true)
  assert.equal(isPrimaryHost('site-abc123-du.a.run.app', PRIMARY), true)
  assert.equal(isPrimaryHost('reputation-site.vercel.app', PRIMARY), true)
  assert.equal(isPrimaryHost('reputation-site-git-main-motionlabs.vercel.app', PRIMARY), true)
  assert.equal(isPrimaryHost(null, PRIMARY), true)
  assert.equal(isPrimaryHost('clinic.example.com', PRIMARY), false)
  assert.equal(isPrimaryHost('evilrun.app.example.com', PRIMARY), false)
  assert.equal(isPrimaryHost('evilvercel.app.example.com', PRIMARY), false)
})

test('isReservedPath: platform-owned paths and static files are reserved', () => {
  assert.equal(isReservedPath('/_next/data/x.json'), true)
  assert.equal(isReservedPath('/api/leads'), true)
  assert.equal(isReservedPath('/landing'), true)
  assert.equal(isReservedPath('/privacy'), true)
  assert.equal(isReservedPath('/terms'), true)
  assert.equal(isReservedPath('/robots.txt'), true)
  assert.equal(isReservedPath('/sitemap.xml'), true)
  assert.equal(isReservedPath('/favicon.ico'), true)
  assert.equal(isReservedPath('/some-image.png'), true)
})

test('isReservedPath: hub paths and /llms.txt are rewritable', () => {
  assert.equal(isReservedPath('/'), false)
  assert.equal(isReservedPath('/contents'), false)
  assert.equal(isReservedPath('/visit'), false)
  assert.equal(isReservedPath('/llms.txt'), false)
  assert.equal(isReservedPath('/treatments/knee'), false)
})

test('decideRewrite: primary host never rewrites', () => {
  assert.equal(decideRewrite('reputation.motionlabs.kr', '/', 'jang-clinic', PRIMARY), null)
  assert.equal(decideRewrite('localhost:3000', '/contents', 'jang-clinic', PRIMARY), null)
  assert.equal(decideRewrite('x.a.run.app', '/', 'jang-clinic', PRIMARY), null)
  assert.equal(decideRewrite('reputation-site.vercel.app', '/', 'jang-clinic', PRIMARY), null)
})

test('resolveRequestHost trusts x-forwarded-host only behind the proxy that overwrites it', () => {
  // Vercel 프록시는 클라이언트가 보낸 x-forwarded-*를 덮어쓰므로 신뢰한다.
  assert.equal(
    resolveRequestHost('reputation-site.vercel.app', 'clinic-b.example.com, proxy.internal', PRIMARY),
    'clinic-b.example.com',
  )
  // GCLB → Cloud Run은 원본 Host를 그대로 넘기고 x-forwarded-host는 손대지 않는다.
  // run.app에서 이 헤더를 믿으면 아무나 병원 도메인을 실어 테넌트 위장이 가능하므로 무시한다.
  assert.equal(
    resolveRequestHost('site-abc123-du.a.run.app', 'clinic-b.example.com', PRIMARY),
    'site-abc123-du.a.run.app',
  )
  // 플랫폼/커스텀 호스트로 직접 들어온 요청도 Host 헤더가 정본이다.
  assert.equal(
    resolveRequestHost('reputation.motionlabs.kr', 'clinic-a.example.com', PRIMARY),
    'reputation.motionlabs.kr',
  )
  assert.equal(
    resolveRequestHost('clinic-a.example.com', 'clinic-b.example.com', PRIMARY),
    'clinic-a.example.com',
  )
  // forwarded 값이 플랫폼 호스트거나 비어 있으면 Host를 그대로 쓴다.
  assert.equal(
    resolveRequestHost('reputation-site.vercel.app', 'reputation.motionlabs.kr', PRIMARY),
    'reputation-site.vercel.app',
  )
  assert.equal(resolveRequestHost('reputation-site.vercel.app', null, PRIMARY), 'reputation-site.vercel.app')
  assert.equal(resolveRequestHost(null, 'clinic-a.example.com', PRIMARY), null)
})

test('resolveRequestHost keeps the port so callers can rebuild the request origin', () => {
  assert.equal(resolveRequestHost('clinic.example.com:8443', null, PRIMARY), 'clinic.example.com:8443')
})

test('custom hosts fail closed when domain resolution is unavailable', () => {
  assert.equal(decideRewrite('unknown.example.com', '/', null, PRIMARY), null)
  assert.equal(decideRewrite('unknown.example.com', '/contents', null, PRIMARY), null)
  assert.equal(shouldFailClosedCustomHost('unknown.example.com', '/', null, PRIMARY), true)
  assert.equal(shouldFailClosedCustomHost('unknown.example.com', '/contents', null, PRIMARY), true)
  assert.equal(shouldFailClosedCustomHost('unknown.example.com', '/api/leads', null, PRIMARY), false)
  assert.equal(shouldFailClosedCustomHost('reputation.motionlabs.kr', '/', null, PRIMARY), false)
})

test('decideRewrite: custom domain root rewrites to /{slug}', () => {
  assert.equal(decideRewrite('clinic.example.com', '/', 'jang-clinic', PRIMARY), '/jang-clinic')
})

test('decideRewrite: paths already under /{slug} pass through', () => {
  assert.equal(decideRewrite('clinic.example.com', '/jang-clinic', 'jang-clinic', PRIMARY), null)
  assert.equal(
    decideRewrite('clinic.example.com', '/jang-clinic/contents', 'jang-clinic', PRIMARY),
    null,
  )
  // slug와 prefix만 같은 다른 경로는 rewrite 대상
  assert.equal(
    decideRewrite('clinic.example.com', '/jang-clinic2', 'jang-clinic', PRIMARY),
    '/jang-clinic/jang-clinic2',
  )
})

test('custom domain redirects exposed internal slug paths to clean public paths', () => {
  assert.equal(
    decideCanonicalRedirect('clinic.example.com', '/jang-clinic', 'jang-clinic', PRIMARY),
    '/',
  )
  assert.equal(
    decideCanonicalRedirect(
      'clinic.example.com',
      '/jang-clinic/contents/post-1',
      'jang-clinic',
      PRIMARY,
    ),
    '/contents/post-1',
  )
  assert.equal(
    decideCanonicalRedirect('clinic.example.com', '/contents', 'jang-clinic', PRIMARY),
    null,
  )
  assert.equal(
    decideCanonicalRedirect(
      'reputation.motionlabs.kr',
      '/jang-clinic/contents',
      'jang-clinic',
      PRIMARY,
    ),
    null,
  )
})

test('decideRewrite: non-reserved root paths get slug prefix', () => {
  assert.equal(
    decideRewrite('clinic.example.com', '/contents', 'jang-clinic', PRIMARY),
    '/jang-clinic/contents',
  )
  assert.equal(
    decideRewrite('clinic.example.com', '/visit', 'jang-clinic', PRIMARY),
    '/jang-clinic/visit',
  )
  assert.equal(
    decideRewrite('clinic.example.com', '/llms.txt', 'jang-clinic', PRIMARY),
    '/jang-clinic/llms.txt',
  )
  assert.equal(
    decideRewrite('clinic.example.com', '/treatments/knee-pain', 'jang-clinic', PRIMARY),
    '/jang-clinic/treatments/knee-pain',
  )
})

test('decideRewrite: reserved paths pass through on custom domain', () => {
  assert.equal(decideRewrite('clinic.example.com', '/robots.txt', 'jang-clinic', PRIMARY), null)
  assert.equal(decideRewrite('clinic.example.com', '/privacy', 'jang-clinic', PRIMARY), null)
  assert.equal(decideRewrite('clinic.example.com', '/_next/data/a.json', 'jang-clinic', PRIMARY), null)
  assert.equal(decideRewrite('clinic.example.com', '/og-image.png', 'jang-clinic', PRIMARY), null)
})

test('decideRewrite: malformed slug from backend is rejected defensively', () => {
  assert.equal(decideRewrite('clinic.example.com', '/', '../etc', PRIMARY), null)
  assert.equal(decideRewrite('clinic.example.com', '/', 'UPPER CASE', PRIMARY), null)
  assert.equal(decideRewrite('clinic.example.com', '/', '', PRIMARY), null)
})
