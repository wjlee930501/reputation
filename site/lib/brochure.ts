import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto'

import { isReasonableKoreanMobilePhone } from './inquiry-lead.ts'
import { containsPatientSensitiveLeadText } from './lead-safety.ts'

/**
 * 서비스 소개서 열람 — 도입문의보다 한 칸 낮은 사다리.
 *
 * 원장이 병원명·성함·휴대폰 세 칸과 동의 하나로 소개서를 바로 열어 본다. 대가로 우리는
 * **누가 어느 페이지를 얼마나 봤는지**를 얻고, 담당자는 그 기록을 들고 먼저 연락할
 * 병원을 고른다. 예전 자동 리포트처럼 "받고 끝"이 되지 않는 이유가 이 기록이다.
 *
 * 이 파일은 순수 함수만 둔다(토큰, 입력 검증, 이벤트 정제, 문서 조립). 라우트는
 * `app/api/brochure/*`와 `app/brochure/doc`에 있다.
 */

export const BROCHURE_COOKIE = 'rp_brochure'
/** 처리방침 제3조의 보유기간(180일)과 맞춘다 — 쿠키가 기록보다 오래 살 이유가 없다. */
export const BROCHURE_COOKIE_MAX_AGE = 180 * 24 * 60 * 60
export const BROCHURE_CONSENT_VERSION = 'v1.2026-09'
export const BROCHURE_PAGE_COUNT = 12

/* ── 열람 토큰 ──────────────────────────────────────────────────────
 * `bt_<무작위 16바이트>.<서명 앞 16자>`. 서명이 있어야 공유 링크를 흉내 내 남의 이름으로
 * 열람 기록을 쌓는 일을 막는다. 비밀값이 없는 환경(로컬)에서는 고정 개발용 키를 쓴다. */

function tokenSecret(): string {
  const configured = (process.env.BROCHURE_TOKEN_SECRET || process.env.SITE_BFF_SECRET || '').trim()
  if (configured) return configured
  return 'reputation-brochure-dev-secret'
}

function sign(id: string, secret = tokenSecret()): string {
  return createHmac('sha256', secret).update(id).digest('base64url').slice(0, 16)
}

export function issueBrochureToken(secret?: string): string {
  const id = `bt_${randomBytes(16).toString('base64url')}`
  return `${id}.${sign(id, secret)}`
}

export function verifyBrochureToken(token: unknown, secret?: string): string | null {
  if (typeof token !== 'string' || token.length > 80) return null
  const match = /^(bt_[A-Za-z0-9_-]{16,32})\.([A-Za-z0-9_-]{16})$/.exec(token)
  if (!match) return null
  const expected = Buffer.from(sign(match[1], secret))
  const given = Buffer.from(match[2])
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) return null
  return token
}

/* ── 게이트 입력 ─────────────────────────────────────────────────── */

export type BrochureLeadInput = {
  hospitalName: string
  directorName: string
  phone: string
  consent: boolean
}

export type BrochureLeadError = 'required' | 'phone' | 'consent' | 'sensitive'

const LIMITS = { hospitalName: 200, directorName: 100, phone: 40 } as const

export function normalizeBrochureLead(raw: Record<string, unknown>): BrochureLeadInput {
  const text = (key: keyof typeof LIMITS) => {
    const value = raw[key]
    return typeof value === 'string' ? value.trim().slice(0, LIMITS[key]) : ''
  }
  return {
    hospitalName: text('hospitalName'),
    directorName: text('directorName'),
    phone: text('phone'),
    consent: raw.consent === true || raw.consent === 'on',
  }
}

export function brochureLeadError(input: BrochureLeadInput): BrochureLeadError | null {
  if (!input.hospitalName || !input.directorName || !input.phone) return 'required'
  if (!isReasonableKoreanMobilePhone(input.phone)) return 'phone'
  if (!input.consent) return 'consent'
  if ([input.hospitalName, input.directorName].some(containsPatientSensitiveLeadText)) return 'sensitive'
  return null
}

export const BROCHURE_LEAD_MESSAGES: Record<BrochureLeadError, string> = {
  required: '병원명, 원장님 성함, 휴대폰 번호를 모두 입력해 주세요.',
  phone: '휴대폰 번호 형식을 확인해 주세요. (예: 010-1234-5678)',
  consent: '소개서 열람을 위해 개인정보 수집·이용에 동의해 주세요.',
  sensitive: '입력값에 환자 정보로 보이는 내용이 있습니다. 병원명과 원장님 성함만 입력해 주세요.',
}

/* ── 열람 이벤트 ─────────────────────────────────────────────────── */

export type BrochureEventKind = 'open' | 'page' | 'complete' | 'cta' | 'share'
const EVENT_KINDS = new Set<BrochureEventKind>(['open', 'page', 'complete', 'cta', 'share'])
/** 한 번에 받는 이벤트 수. 정상 전송은 페이지 전환마다 1~3건이다. */
export const MAX_EVENTS_PER_BATCH = 50
/** 한 페이지 기록 한 건의 상한(30분). 탭을 켜 둔 채 자리를 비운 시간을 잘라낸다. */
export const MAX_PAGE_MS = 30 * 60 * 1000

export type BrochureEvent = {
  kind: BrochureEventKind
  page: number
  ms: number
  mode: 'deck' | 'flow'
  at: string
  detail?: string
}

export type BrochureEventBatch = {
  token: string
  viewer: 'owner' | 'shared'
  device: string
  session: string
  events: BrochureEvent[]
}

const ID_PATTERN = /^[A-Za-z0-9_-]{1,40}$/

/** 브라우저가 보낸 배치를 검증·정제한다. 토큰 서명이 틀리면 통째로 버린다. */
export function sanitizeBrochureEvents(raw: unknown, now = Date.now(), secret?: string): BrochureEventBatch | null {
  if (!raw || typeof raw !== 'object') return null
  const body = raw as Record<string, unknown>
  const token = verifyBrochureToken(body.t, secret)
  if (!token) return null
  const viewer = body.v === 'shared' ? 'shared' : 'owner'
  const device = typeof body.d === 'string' && ID_PATTERN.test(body.d) ? body.d : 'na'
  const session = typeof body.s === 'string' && ID_PATTERN.test(body.s) ? body.s : 'na'
  if (!Array.isArray(body.e)) return null

  const events: BrochureEvent[] = []
  for (const item of body.e.slice(0, MAX_EVENTS_PER_BATCH)) {
    if (!item || typeof item !== 'object') continue
    const e = item as Record<string, unknown>
    if (typeof e.k !== 'string' || !EVENT_KINDS.has(e.k as BrochureEventKind)) continue
    const page = Number(e.p)
    if (!Number.isInteger(page) || page < 0 || page > BROCHURE_PAGE_COUNT) continue
    const ms = Math.min(MAX_PAGE_MS, Math.max(0, Math.round(Number(e.ms) || 0)))
    const at = Number(e.at)
    // 브라우저 시계는 믿지 않는다 — 하루 이상 어긋나면 서버 시각으로 바꾼다.
    const stamp = Number.isFinite(at) && Math.abs(at - now) < 24 * 60 * 60 * 1000 ? at : now
    const event: BrochureEvent = {
      kind: e.k as BrochureEventKind,
      page,
      ms,
      mode: e.m === 'flow' ? 'flow' : 'deck',
      at: new Date(stamp).toISOString(),
    }
    if (typeof e.x === 'string' && ID_PATTERN.test(e.x)) event.detail = e.x
    events.push(event)
  }
  if (events.length === 0) return null
  return { token, viewer, device, session, events }
}

/* ── 문서 조립 ───────────────────────────────────────────────────── */

export type BrochureDocParts = {
  html: string
  mobileCss: string
  bridgeJs: string
}

/**
 * 효진님 원본 HTML에 세로 흐름 CSS·열람 설정·계측 스크립트를 끼워 넣는다.
 * 원본 파일은 건드리지 않는다 — 소개서를 새로 받으면 파일만 바꾸면 된다.
 */
export function assembleBrochureDocument(
  parts: BrochureDocParts,
  config: { token: string | null; viewer: 'owner' | 'shared' },
): string {
  const settings = JSON.stringify({
    token: config.token,
    viewer: config.viewer,
    endpoint: '/api/brochure/events',
  }).replace(/</g, '\\u003c')
  // 첫 그림부터 세로 흐름이어야 좁은 화면에서 축소된 슬라이드가 한 번 번쩍이지 않는다.
  const head =
    `<meta name="robots" content="noindex,nofollow">` +
    `<style>${parts.mobileCss}</style>` +
    `<script>window.__RP_BROCHURE__=${settings};` +
    `try{if(matchMedia('(max-width: 760px)').matches)document.documentElement.classList.add('rp-flow')}catch(e){}</script>`
  const tail = `<script>${parts.bridgeJs}</script>`

  let html = parts.html
  // 치환 문자열의 `$&` 같은 패턴이 해석되지 않도록 함수로 넣는다.
  html = html.includes('</head>') ? html.replace('</head>', () => `${head}</head>`) : head + html
  const bodyEnd = html.lastIndexOf('</body>')
  html = bodyEnd >= 0 ? html.slice(0, bodyEnd) + tail + html.slice(bodyEnd) : html + tail
  return html
}
