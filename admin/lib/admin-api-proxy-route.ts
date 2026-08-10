import { NextRequest, NextResponse } from 'next/server.js'

import {
  adminProxyTimeoutMsForPath,
  buildAdminProxyFetchInit,
  mapAdminProxyFetchError,
} from './admin-proxy.ts'
import { getBackendUrl } from './backend.ts'
import { buildProxyResponse } from './proxy-response.ts'
import { buildSafeAdminProxyPath, hasValidAdminCsrfToken, hasValidSameOrigin } from './security.ts'
import {
  checkAdminSessionRevocation,
  checkAdminSessionRevocationCached,
} from './session-revocation.ts'
import { readSessionToken } from './session.ts'

const ALLOWED_PREFIXES = [
  'hospitals',
  'content',
  'reports',
  'sov',
  'domain',
  'essence',
  'leads',
  'handoffs',
  // 전역 제어 평면 — 병원 단위가 아니라 /admin/operations, /admin/accounts로 열린다.
  // 여기 없으면 백엔드에 라우터가 있어도 프록시가 403으로 끊는다.
  'operations',
  'accounts',
]
const ALLOWED_METHODS = new Set(['GET', 'POST', 'PATCH', 'DELETE'])
const SAFE_IDEMPOTENCY_KEY = /^[A-Za-z0-9_.:-]{1,255}$/

type IdempotencyKeyValidation =
  | { valid: true; value: string | null }
  | { valid: false }

export function validateAdminIdempotencyKey(value: string | null): IdempotencyKeyValidation {
  if (value === null) return { valid: true, value: null }
  if (!SAFE_IDEMPOTENCY_KEY.test(value)) return { valid: false }
  return { valid: true, value }
}

function isLeadRecoveryPath(path: string): boolean {
  const segments = path.split('/')
  return (
    segments.length === 5 &&
    segments[0] === 'leads' &&
    segments[2] === 'diagnoses' &&
    (segments[4] === 'remeasure' || segments[4] === 'rebuild-report')
  )
}

type AdminApiProxyContext = {
  params: Promise<{ path: string[] }>
}

function jsonNoStore(body: unknown, init?: ResponseInit): NextResponse {
  const res = NextResponse.json(body, init)
  res.headers.set('Cache-Control', 'no-store, private')
  return res
}

function textNoStore(body: string, init?: ResponseInit): NextResponse {
  const res = new NextResponse(body, init)
  res.headers.set('Cache-Control', 'no-store, private')
  return res
}

function backendUrlOrNull(): string | null {
  try {
    return getBackendUrl()
  } catch (error) {
    if (error instanceof Error) return null
    throw error
  }
}

export async function handleAdminApiProxy(
  req: NextRequest,
  { params }: AdminApiProxyContext,
): Promise<Response> {
  if (!ALLOWED_METHODS.has(req.method)) {
    return textNoStore('Method Not Allowed', { status: 405 })
  }

  const { path: pathSegments } = await params
  const adminKey = process.env.ADMIN_SECRET_KEY || ''
  if (!adminKey) {
    return jsonNoStore({ error: 'Server misconfigured' }, { status: 500 })
  }
  const sessionSecret = process.env.ADMIN_SESSION_SECRET
  if (!sessionSecret) {
    return jsonNoStore({ error: 'Server misconfigured' }, { status: 500 })
  }

  if (!hasValidSameOrigin(req)) {
    return textNoStore('Forbidden', { status: 403 })
  }

  const sessionToken = req.cookies.get('admin_session')?.value
  if (!sessionToken) {
    return jsonNoStore({ error: 'Unauthorized' }, { status: 401 })
  }

  const session = await readSessionToken(sessionToken, sessionSecret)
  if (!session) {
    return jsonNoStore({ error: 'Unauthorized' }, { status: 401 })
  }
  if (!hasValidAdminCsrfToken(req, session)) {
    return textNoStore('Forbidden', { status: 403 })
  }

  const path = buildSafeAdminProxyPath(pathSegments, ALLOWED_PREFIXES)
  if (!path) {
    return textNoStore('Forbidden', { status: 403 })
  }

  const isWriteMethod = req.method !== 'GET' && req.method !== 'HEAD'
  const idempotency = validateAdminIdempotencyKey(
    isWriteMethod ? req.headers.get('idempotency-key') : null,
  )
  if (
    !idempotency.valid ||
    (isWriteMethod && isLeadRecoveryPath(path) && idempotency.value === null)
  ) {
    return jsonNoStore({ error: 'Invalid Idempotency-Key' }, { status: 400 })
  }

  const backendUrl = backendUrlOrNull()
  if (!backendUrl) {
    return jsonNoStore({ error: 'Server misconfigured' }, { status: 500 })
  }

  // 읽기(GET/HEAD)만 짧은 TTL 폐기 캐시를 사용해 대시보드의 병렬 호출 왕복을 줄인다.
  // 상태 변경 요청(POST/PATCH/DELETE)은 캐시를 신뢰하지 않고 매번 백엔드로 폐기 여부를
  // 재확인한다 — 로그아웃/강제 폐기 직후에도 쓰기가 캐시된 'active'로 통과하지 않도록.
  const isReadMethod = req.method === 'GET' || req.method === 'HEAD'
  const checkRevocation = isReadMethod
    ? checkAdminSessionRevocationCached
    : checkAdminSessionRevocation
  const revocationStatus = await checkRevocation({
    backendUrl,
    adminKey,
    sessionToken,
    accountId: session.accountId,
    issuedAtMs: session.issuedAt,
  })
  if (revocationStatus === 'unavailable') {
    return jsonNoStore({ error: 'Admin session state unavailable' }, { status: 503 })
  }
  if (revocationStatus === 'revoked') {
    return jsonNoStore({ error: 'Unauthorized' }, { status: 401 })
  }

  const url = new URL(`/api/v1/admin/${path}`, backendUrl)

  req.nextUrl.searchParams.forEach((value, key) => {
    url.searchParams.set(key, value)
  })

  const headers: Record<string, string> = {
    'X-Admin-Key': adminKey,
    'X-Admin-Actor': session.email,
  }

  const contentType = req.headers.get('content-type')
  if (contentType) {
    headers['content-type'] = contentType
  }
  if (idempotency.valid && idempotency.value !== null) {
    headers['Idempotency-Key'] = idempotency.value
  }

  let body: ArrayBuffer | undefined
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    body = await req.arrayBuffer()
  }

  const timeoutMs = adminProxyTimeoutMsForPath(path)

  try {
    const fetchOptions = buildAdminProxyFetchInit({ method: req.method, headers, body, timeoutMs })
    const res = await fetch(url.toString(), fetchOptions)
    return buildProxyResponse(res)
  } catch (error) {
    const mapped = mapAdminProxyFetchError(error)
    return jsonNoStore({ error: mapped.error }, { status: mapped.status })
  }
}
