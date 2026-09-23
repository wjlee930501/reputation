import { readFile } from 'node:fs/promises'
import path from 'node:path'

import { decorateSourcePath, deserializeAttribution } from './ad-attribution.ts'
import {
  BROCHURE_CONSENT_VERSION,
  BROCHURE_COOKIE,
  BROCHURE_COOKIE_MAX_AGE,
  BROCHURE_LEAD_MESSAGES,
  assembleBrochureDocument,
  brochureLeadError,
  issueBrochureToken,
  normalizeBrochureLead,
  sanitizeBrochureEvents,
  verifyBrochureToken,
} from './brochure.ts'
import { getApiBase } from './config.ts'
import { buildLeadOutboundHeaders, isLeadValidationUpstreamStatus } from './leads-proxy.ts'
import { BodyTooLargeError, readJsonBodyWithLimit } from './request-body.ts'

/**
 * 소개서 라우트 셋.
 *
 *   POST /api/brochure/leads   게이트 입력 → 백엔드 `/brochure-leads`, 열람 쿠키 발급
 *   POST /api/brochure/events  열람 기록 → 백엔드 `/brochure-events` (sendBeacon)
 *   GET  /brochure/doc         소개서 문서. 쿠키나 공유 토큰이 있을 때만 내보낸다.
 *
 * ## 백엔드가 아직 받지 못할 때
 *
 * 백엔드 엔드포인트가 없거나 죽어 있어도 **원장의 열람은 막지 않는다.** 소개서를 보겠다고
 * 입력까지 한 사람에게 오류 화면을 보여주는 것이 가장 나쁜 결과다. 대신 응답에
 * `stored: false`를 싣고 서버 로그에 남긴다. 리드가 유실될 수 있으므로 홈의 진입 링크는
 * `NEXT_PUBLIC_BROCHURE_ENABLED=1`일 때만 노출한다(백엔드 준비 후 켠다).
 */

const MAX_LEAD_BYTES = 8 * 1024
const MAX_EVENT_BYTES = 32 * 1024

function cookieHeader(token: string): string {
  const parts = [
    `${BROCHURE_COOKIE}=${token}`,
    'Path=/',
    `Max-Age=${BROCHURE_COOKIE_MAX_AGE}`,
    'HttpOnly',
    'SameSite=Lax',
  ]
  if (process.env.NODE_ENV === 'production') parts.push('Secure')
  return parts.join('; ')
}

export function readBrochureCookie(cookieHeaderValue: string | null): string | null {
  if (!cookieHeaderValue) return null
  for (const part of cookieHeaderValue.split(';')) {
    const [name, ...rest] = part.trim().split('=')
    if (name === BROCHURE_COOKIE) return verifyBrochureToken(rest.join('='))
  }
  return null
}

/* ── POST /api/brochure/leads ─────────────────────────────────────── */

export async function postBrochureLead(request: Request): Promise<Response> {
  let raw: unknown
  try {
    raw = await readJsonBodyWithLimit(request, MAX_LEAD_BYTES)
  } catch (error) {
    const status = error instanceof BodyTooLargeError ? 413 : 400
    return Response.json({ ok: false, error: '입력값을 읽지 못했습니다.' }, { status })
  }
  if (!raw || typeof raw !== 'object') {
    return Response.json({ ok: false, error: '입력값을 읽지 못했습니다.' }, { status: 400 })
  }
  const body = raw as Record<string, unknown>

  // 허니팟 — 사람이 보지 못하는 칸이 채워져 있으면 조용히 성공처럼 돌려보낸다.
  if (typeof body.website === 'string' && body.website.trim()) {
    return Response.json({ ok: true, stored: false })
  }

  const input = normalizeBrochureLead(body)
  const problem = brochureLeadError(input)
  if (problem) {
    return Response.json({ ok: false, error: BROCHURE_LEAD_MESSAGES[problem], field: problem }, { status: 400 })
  }

  // 이미 열람 쿠키가 있으면 같은 토큰을 이어 쓴다 — 같은 원장이 두 번 입력해도 기록이
  // 두 사람으로 갈라지지 않는다.
  const token = readBrochureCookie(request.headers.get('cookie')) ?? issueBrochureToken()
  const sourcePathRaw = typeof body.sourcePath === 'string' && body.sourcePath.startsWith('/') ? body.sourcePath : '/brochure'
  const attribution = typeof body.attribution === 'string' ? deserializeAttribution(body.attribution) : null

  const payload = {
    token,
    hospital_name: input.hospitalName,
    director_name: input.directorName,
    phone: input.phone,
    privacy: true,
    consent_version: BROCHURE_CONSENT_VERSION,
    source_path: decorateSourcePath(sourcePathRaw.slice(0, 500), attribution),
  }

  let stored = false
  try {
    const upstream = await fetch(`${getApiBase(true)}/brochure-leads`, {
      method: 'POST',
      headers: buildLeadOutboundHeaders(request.headers),
      body: JSON.stringify(payload),
      cache: 'no-store',
    })
    if (upstream.ok) {
      stored = true
    } else if (upstream.status !== 404 && isLeadValidationUpstreamStatus(upstream.status)) {
      // 백엔드가 입력을 거절한 경우만 원장에게 되돌린다(404는 "엔드포인트 없음"이라 제외).
      return Response.json({ ok: false, error: '입력하신 내용을 다시 확인해 주세요.' }, { status: 400 })
    } else {
      console.error(`[brochure] lead upstream responded ${upstream.status}; viewing allowed, lead not stored`)
    }
  } catch (error) {
    console.error('[brochure] lead upstream unreachable; viewing allowed, lead not stored', error)
  }

  return Response.json(
    { ok: true, stored, token },
    { headers: { 'Set-Cookie': cookieHeader(token), 'Cache-Control': 'no-store' } },
  )
}

/* ── POST /api/brochure/events ────────────────────────────────────── */

export async function postBrochureEvents(request: Request): Promise<Response> {
  let raw: unknown
  try {
    raw = await readJsonBodyWithLimit(request, MAX_EVENT_BYTES)
  } catch {
    return new Response(null, { status: 204 })
  }
  const batch = sanitizeBrochureEvents(raw)
  // 계측은 조용히 실패한다 — 어떤 경우에도 브라우저에는 204만 돌려준다.
  if (!batch) return new Response(null, { status: 204 })

  try {
    const upstream = await fetch(`${getApiBase(true)}/brochure-events`, {
      method: 'POST',
      headers: buildLeadOutboundHeaders(request.headers),
      body: JSON.stringify(batch),
      cache: 'no-store',
    })
    if (!upstream.ok) console.error(`[brochure] events upstream responded ${upstream.status}`)
  } catch (error) {
    console.error('[brochure] events upstream unreachable', error)
  }
  return new Response(null, { status: 204 })
}

/* ── GET /brochure/doc ────────────────────────────────────────────── */

const CONTENT_DIR = path.join(process.cwd(), 'content', 'brochure')
let partsCache: Promise<{ html: string; mobileCss: string; bridgeJs: string }> | null = null

function loadParts() {
  if (!partsCache || process.env.NODE_ENV !== 'production') {
    partsCache = Promise.all([
      readFile(path.join(CONTENT_DIR, 'reputation-brochure.html'), 'utf8'),
      readFile(path.join(CONTENT_DIR, 'mobile.css'), 'utf8'),
      readFile(path.join(CONTENT_DIR, 'bridge.js'), 'utf8'),
    ]).then(([html, mobileCss, bridgeJs]) => ({ html, mobileCss, bridgeJs }))
    partsCache.catch(() => {
      partsCache = null
    })
  }
  return partsCache
}

const LOCKED_PAGE = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="robots" content="noindex,nofollow"><title>Re:putation 서비스 소개서</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#000;color:#fff;font-family:Pretendard,system-ui,sans-serif;text-align:center;word-break:keep-all;padding:24px}a{color:#fff;font-weight:700}</style></head>
<body><p>소개서는 <a href="/brochure" target="_top">열람 신청</a> 후 보실 수 있습니다.</p></body></html>`

export async function getBrochureDocument(request: Request): Promise<Response> {
  const url = new URL(request.url)
  const shared = verifyBrochureToken(url.searchParams.get('s'))
  const owner = readBrochureCookie(request.headers.get('cookie'))
  // 쿠키가 있으면 공유 링크로 열어도 본인 열람으로 본다(본인이 공유 링크를 눌러 본 경우).
  const token = owner ?? shared
  const headers = {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'private, no-store',
    'X-Robots-Tag': 'noindex, nofollow',
  }
  if (!token) return new Response(LOCKED_PAGE, { status: 403, headers })

  const parts = await loadParts()
  const html = assembleBrochureDocument(parts, { token, viewer: owner ? 'owner' : 'shared' })
  return new Response(html, { status: 200, headers })
}
