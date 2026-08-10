import { getApiBase } from './config.ts'
import {
  getPrimaryHostnames,
  isPrimaryHost,
  normalizeHostname,
  resolveRequestHost,
} from './host-routing.ts'

export type ReputationHealthPayload = {
  readonly hospital_id: string
  readonly slug: string
  readonly canonical_host: string
  readonly release: string
}

const PRIVATE_NO_STORE = 'no-store, private'

type TenantLookup = Omit<ReputationHealthPayload, 'release'>

function isTenantLookup(value: unknown): value is TenantLookup {
  if (typeof value !== 'object' || value === null) return false
  const hospitalId = Reflect.get(value, 'hospital_id')
  const slug = Reflect.get(value, 'slug')
  const canonicalHost = Reflect.get(value, 'canonical_host')
  return (
    typeof hospitalId === 'string' &&
    hospitalId.length > 0 &&
    typeof slug === 'string' &&
    slug.length > 0 &&
    typeof canonicalHost === 'string' &&
    canonicalHost.length > 0
  )
}

export function publicRelease(): string {
  return process.env.K_REVISION?.trim() || process.env.NEXT_PUBLIC_APP_RELEASE?.trim() || 'local'
}

export async function resolveReputationHealth(
  headers: Headers,
  fetcher: typeof fetch = fetch,
): Promise<Response> {
  const primaryHostnames = getPrimaryHostnames(process.env.NEXT_PUBLIC_SITE_URL)
  const requestHost = resolveRequestHost(
    headers.get('host'),
    headers.get('x-forwarded-host'),
    primaryHostnames,
  )
  const hostname = normalizeHostname(requestHost)
  if (!hostname || isPrimaryHost(hostname, primaryHostnames)) {
    return new Response('Not found', { status: 404, headers: { 'cache-control': PRIVATE_NO_STORE } })
  }

  const apiBase = getApiBase(false)
  if (!apiBase) {
    return new Response('Tenant lookup unavailable', {
      status: 503,
      headers: { 'cache-control': PRIVATE_NO_STORE, 'retry-after': '30' },
    })
  }

  let upstream: Response
  try {
    upstream = await fetcher(
      `${apiBase}/site/hospitals/health/by-domain/${encodeURIComponent(hostname)}`,
      {
        cache: 'no-store',
        redirect: 'manual',
        signal: AbortSignal.timeout(5_000),
      },
    )
  } catch (error) {
    if (!(error instanceof DOMException || error instanceof TypeError)) throw error
    return new Response('Tenant lookup unavailable', {
      status: 503,
      headers: { 'cache-control': PRIVATE_NO_STORE, 'retry-after': '30' },
    })
  }
  if (!upstream.ok) {
    const status = upstream.status === 404 ? 404 : 503
    return new Response(status === 404 ? 'Not found' : 'Tenant lookup unavailable', {
      status,
      headers: { 'cache-control': PRIVATE_NO_STORE, ...(status === 503 ? { 'retry-after': '30' } : {}) },
    })
  }

  let lookup: unknown
  try {
    lookup = await upstream.json()
  } catch {
    return new Response('Tenant identity unavailable', {
      status: 502,
      headers: { 'cache-control': PRIVATE_NO_STORE },
    })
  }
  if (!isTenantLookup(lookup) || normalizeHostname(lookup.canonical_host) !== hostname) {
    return new Response('Tenant identity mismatch', {
      status: 502,
      headers: { 'cache-control': PRIVATE_NO_STORE },
    })
  }
  const payload: ReputationHealthPayload = { ...lookup, canonical_host: hostname, release: publicRelease() }
  return Response.json(payload, { headers: { 'cache-control': PRIVATE_NO_STORE } })
}
