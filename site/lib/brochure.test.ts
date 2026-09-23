import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  BROCHURE_COOKIE,
  MAX_PAGE_MS,
  assembleBrochureDocument,
  brochureLeadError,
  issueBrochureToken,
  normalizeBrochureLead,
  sanitizeBrochureEvents,
  verifyBrochureToken,
} from './brochure.ts'
import { getBrochureDocument, postBrochureEvents, postBrochureLead } from './brochure-routes.ts'

const html = readFileSync(new URL('../content/brochure/reputation-brochure.html', import.meta.url), 'utf8')

/* ── 토큰 ─────────────────────────────────────────────────────────── */

test('an issued token verifies and a tampered one does not', () => {
  const token = issueBrochureToken('secret-a')
  assert.equal(verifyBrochureToken(token, 'secret-a'), token)
  assert.equal(verifyBrochureToken(token, 'secret-b'), null, '다른 비밀값으로 만든 서명은 통과하면 안 된다')
  const [id, signature] = token.split('.')
  const flipped = signature[0] === 'A' ? `B${signature.slice(1)}` : `A${signature.slice(1)}`
  assert.equal(verifyBrochureToken(`${id}.${flipped}`, 'secret-a'), null)
  assert.equal(verifyBrochureToken('bt_short.abc', 'secret-a'), null)
  assert.equal(verifyBrochureToken(undefined, 'secret-a'), null)
})

/* ── 게이트 입력 ──────────────────────────────────────────────────── */

test('the gate asks for three fields and an explicit consent', () => {
  const ok = normalizeBrochureLead({ hospitalName: ' 서울정형외과 ', directorName: '김원장', phone: '010-1234-5678', consent: true })
  assert.equal(ok.hospitalName, '서울정형외과')
  assert.equal(brochureLeadError(ok), null)

  assert.equal(brochureLeadError({ ...ok, hospitalName: '' }), 'required')
  assert.equal(brochureLeadError({ ...ok, phone: '02-123-4567' }), 'phone')
  // 동의는 명시적으로 받는다 — 값이 없으면 동의하지 않은 것이다.
  assert.equal(brochureLeadError(normalizeBrochureLead({ ...ok, consent: undefined })), 'consent')
  assert.equal(brochureLeadError(normalizeBrochureLead({ ...ok, consent: 'yes' })), 'consent')
})

/* ── 열람 이벤트 ──────────────────────────────────────────────────── */

test('event batches keep only known kinds and clamp page time', () => {
  const token = issueBrochureToken('s')
  const now = Date.parse('2026-09-23T00:00:00Z')
  const batch = sanitizeBrochureEvents(
    {
      t: token,
      v: 'shared',
      d: 'dev1',
      s: 'sess1',
      e: [
        { k: 'page', p: 11, ms: 90_000, m: 'flow', at: now },
        { k: 'page', p: 3, ms: 10 * 60 * 60 * 1000, at: now }, // 10시간 → 30분으로 자른다
        { k: 'hack', p: 1 },
        { k: 'page', p: 99, ms: 1 },
        { k: 'cta', p: 12, ms: 0, x: 'closing', at: now - 3 * 24 * 60 * 60 * 1000 },
      ],
    },
    now,
    's',
  )
  assert.ok(batch)
  assert.equal(batch.viewer, 'shared')
  assert.equal(batch.events.length, 3)
  assert.equal(batch.events[0].ms, 90_000)
  assert.equal(batch.events[0].mode, 'flow')
  assert.equal(batch.events[1].ms, MAX_PAGE_MS)
  assert.equal(batch.events[2].detail, 'closing')
  // 사흘 어긋난 브라우저 시계는 서버 시각으로 바뀐다.
  assert.equal(batch.events[2].at, new Date(now).toISOString())
})

test('events signed for someone else are dropped entirely', () => {
  const forged = issueBrochureToken('attacker')
  assert.equal(sanitizeBrochureEvents({ t: forged, e: [{ k: 'page', p: 1, ms: 1 }] }, Date.now(), 'real'), null)
})

/* ── 문서 ─────────────────────────────────────────────────────────── */

test('the brochure routes readers to the inquiry, not the retired diagnosis funnel', () => {
  assert.doesNotMatch(html, /ai-diagnosis/, '무료 진단(/ai-diagnosis)으로 가는 링크가 남아 있다')
  assert.doesNotMatch(html, /순차 발송|선착순|병원당 1회/, '예전 자동 발송 퍼널의 문구가 남아 있다')
  const ctas = [...html.matchAll(/<a[^>]*data-rp-cta="[^"]+"[^>]*>/g)].map((m) => m[0])
  assert.ok(ctas.length >= 3, '소개서 안에 도입문의 CTA가 있어야 한다')
  for (const cta of ctas) {
    assert.match(cta, /href="\/contact"/)
    assert.match(cta, /target="_top"/, 'iframe 안에서 도입문의를 열면 폼이 소개서 틀 안에 갇힌다')
  }
})

test('the brochure keeps our flat rules: no shadows, no glow', () => {
  assert.doesNotMatch(html, /box-shadow:(?!none)/)
  assert.doesNotMatch(html, /radial-gradient/)
})

test('the brochure does not overstate what the AI list decides', () => {
  assert.doesNotMatch(html, /비교 후보에서 제외/)
  assert.doesNotMatch(html, /목록 안에서 결정/)
})

test('assembling the document injects config, flow css and the tracker', () => {
  const out = assembleBrochureDocument(
    { html: '<html><head></head><body><p>x</p></body></html>', mobileCss: '.rp-flow{}', bridgeJs: 'void 0 /* $& */' },
    { token: 'bt_x</script>', viewer: 'owner' },
  )
  assert.match(out, /<style>\.rp-flow\{\}<\/style>/)
  assert.match(out, /window\.__RP_BROCHURE__=/)
  assert.doesNotMatch(out, /bt_x<\/script>/, '설정값의 < 는 이스케이프되어야 한다')
  assert.match(out, /void 0 \/\* \$& \*\/<\/script><\/body>/, '치환 패턴이 해석되면 스크립트가 깨진다')
})

/* ── 라우트 ───────────────────────────────────────────────────────── */

test('the document is locked without a cookie or a share token', async () => {
  const res = await getBrochureDocument(new Request('http://x/brochure/doc'))
  assert.equal(res.status, 403)
  assert.equal(res.headers.get('x-robots-tag'), 'noindex, nofollow')
})

test('the document opens with a valid cookie and marks the viewer', async () => {
  const token = issueBrochureToken()
  const own = await getBrochureDocument(
    new Request('http://x/brochure/doc', { headers: { cookie: `a=1; ${BROCHURE_COOKIE}=${token}` } }),
  )
  assert.equal(own.status, 200)
  assert.match(await own.text(), /"viewer":"owner"/)

  const shared = await getBrochureDocument(new Request(`http://x/brochure/doc?s=${encodeURIComponent(token)}`))
  assert.equal(shared.status, 200)
  assert.match(await shared.text(), /"viewer":"shared"/)
})

async function withFetch<T>(impl: typeof fetch, run: () => Promise<T>): Promise<T> {
  const original = globalThis.fetch
  const originalError = console.error
  globalThis.fetch = impl
  console.error = () => undefined
  try {
    return await run()
  } finally {
    globalThis.fetch = original
    console.error = originalError
  }
}

function leadRequest(body: Record<string, unknown>) {
  return new Request('http://x/api/brochure/leads', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

const VALID = { hospitalName: '서울정형외과', directorName: '김원장', phone: '010-1234-5678', consent: true }

test('a valid lead sets the viewing cookie and forwards to the backend', async () => {
  let forwarded: Record<string, unknown> | null = null
  const res = await withFetch(
    (async (url: string, init: RequestInit) => {
      assert.match(String(url), /\/brochure-leads$/)
      forwarded = JSON.parse(String(init.body))
      return new Response('{}', { status: 201 })
    }) as typeof fetch,
    () => postBrochureLead(leadRequest(VALID)),
  )
  assert.equal(res.status, 200)
  const data = (await res.json()) as { stored: boolean; token: string }
  assert.equal(data.stored, true)
  assert.ok(verifyBrochureToken(data.token))
  assert.match(res.headers.get('set-cookie') ?? '', new RegExp(`^${BROCHURE_COOKIE}=bt_.*HttpOnly`))
  assert.ok(forwarded)
  assert.equal((forwarded as Record<string, unknown>).hospital_name, '서울정형외과')
  assert.equal((forwarded as Record<string, unknown>).privacy, true)
})

test('viewing still opens when the backend cannot store the lead yet', async () => {
  const res = await withFetch(
    (async () => new Response('Not Found', { status: 404 })) as typeof fetch,
    () => postBrochureLead(leadRequest(VALID)),
  )
  assert.equal(res.status, 200)
  assert.equal(((await res.json()) as { stored: boolean }).stored, false)
  assert.ok(res.headers.get('set-cookie'))
})

test('an invalid lead is rejected before reaching the backend', async () => {
  let called = false
  const res = await withFetch(
    (async () => {
      called = true
      return new Response('{}')
    }) as typeof fetch,
    () => postBrochureLead(leadRequest({ ...VALID, consent: false })),
  )
  assert.equal(res.status, 400)
  assert.equal(called, false)
})

test('the events endpoint always answers 204, even when the backend is down', async () => {
  const token = issueBrochureToken()
  const res = await withFetch(
    (async () => {
      throw new Error('down')
    }) as typeof fetch,
    () =>
      postBrochureEvents(
        new Request('http://x/api/brochure/events', {
          method: 'POST',
          body: JSON.stringify({ t: token, e: [{ k: 'page', p: 2, ms: 1000 }] }),
        }),
      ),
  )
  assert.equal(res.status, 204)
})
