import { getApiBase } from './config.ts'
import { buildLeadOutboundHeaders } from './leads-proxy.ts'
import { BodyTooLargeError, readBodyBytesWithLimit, readJsonBodyWithLimit } from './request-body.ts'

/**
 * 원장 사전 설문 프록시 — 브라우저는 같은 출처(`/api/doctor-survey/…`)만 부르고 서버가 백엔드로 넘긴다.
 *
 * 개인에게 발급된 링크라 응답에 no-store·noindex·no-referrer를 붙인다(CDN이 답을 저장하지 않게).
 * 방문자 IP와 BFF 비밀값은 도입문의 프록시와 같은 방식으로 실어 백엔드 레이트 리밋이 방문자 기준이 되게 한다.
 */

const GUARD = {
  'Cache-Control': 'no-store',
  'X-Robots-Tag': 'noindex, nofollow',
  'Referrer-Policy': 'no-referrer',
} as const

const MAX_BODY_BYTES = 300 * 1024
// 파일 한 개 12MB + multipart 머리. 백엔드도 같은 한도로 다시 막는다.
const MAX_FILE_BODY_BYTES = 12 * 1024 * 1024 + 64 * 1024
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{20,100}$/
const FILE_ID_PATTERN = /^[0-9a-f]{32}$/

/** 허용하는 (메서드, 동작) 쌍. 그 밖은 404다. */
const ROUTES: Record<string, string | null> = {
  'GET:': '',
  'POST:consent': '/consent',
  'PUT:answers': '/answers',
  'POST:submit': '/submit',
  'POST:files': '/files',
}

export function surveyUpstreamPath(method: string, token: string, action: string[] | undefined): string | null {
  if (!TOKEN_PATTERN.test(token)) return null
  const parts = action ?? []
  if (method === 'GET' && parts.length === 2 && parts[0] === 'files' && FILE_ID_PATTERN.test(parts[1])) {
    return `/doctor-survey/${encodeURIComponent(token)}/files/${parts[1]}`
  }
  const suffix = ROUTES[`${method}:${parts.join('/')}`]
  if (suffix === undefined || suffix === null) return null
  return `/doctor-survey/${encodeURIComponent(token)}${suffix}`
}

function isFileUpload(method: string, action: string[] | undefined): boolean {
  return method === 'POST' && (action ?? []).join('/') === 'files'
}

function isFileView(method: string, action: string[] | undefined): boolean {
  return method === 'GET' && (action ?? [])[0] === 'files'
}

function json(payload: unknown, status: number): Response {
  return Response.json(payload, { status, headers: GUARD })
}

export async function proxySurveyRequest(
  request: Request,
  params: Promise<{ token: string; action?: string[] }>,
): Promise<Response> {
  const { token, action } = await params
  const path = surveyUpstreamPath(request.method, token, action)
  if (!path) return json({ detail: '유효하지 않은 링크입니다.' }, 404)

  if (isFileUpload(request.method, action)) return proxyFileUpload(request, path)
  if (isFileView(request.method, action)) return proxyFileView(request, path)

  let body: string | undefined
  if (request.method !== 'GET') {
    try {
      body = JSON.stringify(await readJsonBodyWithLimit(request, MAX_BODY_BYTES))
    } catch (error) {
      if (error instanceof BodyTooLargeError) {
        return json({ detail: '답이 너무 깁니다. 긴 답을 줄여 주세요.' }, 413)
      }
      return json({ detail: '입력 형식을 확인할 수 없습니다. 화면을 새로고침해 주세요.' }, 400)
    }
  }

  let upstream: Response
  try {
    upstream = await fetch(`${getApiBase()}${path}`, {
      method: request.method,
      headers: buildLeadOutboundHeaders(request.headers),
      body,
      cache: 'no-store',
    })
  } catch {
    return json({ detail: '잠시 연결이 원활하지 않습니다. 잠시 뒤 다시 시도해 주세요.' }, 502)
  }
  const payload = await upstream.json().catch(() => ({}))
  return json(payload, upstream.status)
}

async function proxyFileUpload(request: Request, path: string): Promise<Response> {
  const declared = Number(request.headers.get('content-length') ?? '0')
  if (declared > MAX_FILE_BODY_BYTES) {
    return json({ detail: '파일이 너무 큽니다. 한 파일에 12MB까지 올릴 수 있습니다.' }, 413)
  }
  let bytes: Uint8Array
  try {
    bytes = await readBodyBytesWithLimit(request, MAX_FILE_BODY_BYTES)
  } catch (error) {
    if (error instanceof BodyTooLargeError) {
      return json({ detail: '파일이 너무 큽니다. 한 파일에 12MB까지 올릴 수 있습니다.' }, 413)
    }
    return json({ detail: '파일을 받지 못했습니다. 다시 올려 주세요.' }, 400)
  }
  const headers = buildLeadOutboundHeaders(request.headers)
  headers['Content-Type'] = request.headers.get('content-type') ?? 'application/octet-stream'
  const body = new ArrayBuffer(bytes.byteLength)
  new Uint8Array(body).set(bytes)
  let upstream: Response
  try {
    upstream = await fetch(`${getApiBase()}${path}`, { method: 'POST', headers, body, cache: 'no-store' })
  } catch {
    return json({ detail: '잠시 연결이 원활하지 않습니다. 잠시 뒤 다시 올려 주세요.' }, 502)
  }
  const payload = await upstream.json().catch(() => ({}))
  return json(payload, upstream.status)
}

async function proxyFileView(request: Request, path: string): Promise<Response> {
  let upstream: Response
  try {
    upstream = await fetch(`${getApiBase()}${path}`, {
      headers: buildLeadOutboundHeaders(request.headers),
      cache: 'no-store',
      redirect: 'follow',
    })
  } catch {
    return json({ detail: '파일을 불러오지 못했습니다.' }, 502)
  }
  if (!upstream.ok) return json({ detail: '파일을 찾을 수 없습니다.' }, upstream.status === 404 ? 404 : 502)
  return new Response(await upstream.arrayBuffer(), {
    status: 200,
    headers: {
      ...GUARD,
      'Content-Type': upstream.headers.get('content-type') ?? 'application/octet-stream',
      'X-Content-Type-Options': 'nosniff',
      'Content-Security-Policy': "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
    },
  })
}
