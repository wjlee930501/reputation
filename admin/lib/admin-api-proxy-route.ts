import { NextRequest, NextResponse } from 'next/server.js'

import { buildAdminProxyFetchInit, mapAdminProxyFetchError } from './admin-proxy.ts'
import { getBackendUrl } from './backend.ts'
import { buildProxyResponse } from './proxy-response.ts'
import { buildSafeAdminProxyPath, hasValidSameOrigin } from './security.ts'
import { readSessionToken } from './session.ts'

const ALLOWED_PREFIXES = ['hospitals', 'content', 'reports', 'sov', 'domain', 'essence', 'leads']
const ALLOWED_METHODS = new Set(['GET', 'POST', 'PATCH', 'DELETE'])

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
  const session = sessionToken ? await readSessionToken(sessionToken, sessionSecret) : null
  if (!session) {
    return jsonNoStore({ error: 'Unauthorized' }, { status: 401 })
  }

  const path = buildSafeAdminProxyPath(pathSegments, ALLOWED_PREFIXES)
  if (!path) {
    return textNoStore('Forbidden', { status: 403 })
  }

  let backendUrl: string
  try {
    backendUrl = getBackendUrl()
  } catch {
    return jsonNoStore({ error: 'Server misconfigured' }, { status: 500 })
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

  let body: ArrayBuffer | undefined
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    body = await req.arrayBuffer()
  }

  try {
    const fetchOptions = buildAdminProxyFetchInit({ method: req.method, headers, body })
    const res = await fetch(url.toString(), fetchOptions)
    return buildProxyResponse(res)
  } catch (error) {
    const mapped = mapAdminProxyFetchError(error)
    return jsonNoStore({ error: mapped.error }, { status: mapped.status })
  }
}
