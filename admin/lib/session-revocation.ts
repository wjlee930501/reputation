import { hashSessionToken } from './session.ts'

export type AdminSessionRevocationStatus = 'active' | 'revoked' | 'unavailable'

export const ADMIN_SESSION_REVOCATION_TIMEOUT_MS = 3_000

interface CheckAdminSessionRevocationOptions {
  backendUrl: string
  adminKey: string
  sessionToken: string
}

interface RevokeAdminSessionOptions extends CheckAdminSessionRevocationOptions {
  expiresAtMs: number
}

function adminAuthUrl(backendUrl: string, path: string): string {
  return new URL(`/api/v1/admin/auth/${path}`, backendUrl).toString()
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function buildRevocationSignal(): AbortSignal {
  return AbortSignal.timeout(ADMIN_SESSION_REVOCATION_TIMEOUT_MS)
}

function isExpectedRevocationFailure(error: unknown): boolean {
  return error instanceof DOMException || error instanceof TypeError || error instanceof SyntaxError
}

export async function checkAdminSessionRevocation({
  backendUrl,
  adminKey,
  sessionToken,
}: CheckAdminSessionRevocationOptions): Promise<AdminSessionRevocationStatus> {
  const tokenHash = await hashSessionToken(sessionToken)
  try {
    const res = await fetch(adminAuthUrl(backendUrl, `sessions/${tokenHash}/revocation`), {
      headers: { 'X-Admin-Key': adminKey },
      cache: 'no-store',
      signal: buildRevocationSignal(),
    })
    if (!res.ok) return 'unavailable'

    const data: unknown = await res.json()
    if (!isRecord(data) || typeof data.revoked !== 'boolean') return 'unavailable'
    return data.revoked ? 'revoked' : 'active'
  } catch (error) {
    if (!isExpectedRevocationFailure(error)) throw error
    return 'unavailable'
  }
}

// 대시보드처럼 한 번의 페이지 로드에서 Admin API를 병렬로 여러 번 호출할 때마다
// 백엔드로 폐기 확인 왕복(최대 3초 타임아웃)이 반복되면 지연이 증폭되고
// 백엔드가 느려지면 Admin 전체가 503이 된다. '활성(active)' 결과만 토큰 해시 키로
// 짧은 TTL 동안 캐시한다 — 폐기(revoked)와 확인 실패(unavailable)는 절대 캐시하지 않아
// fail-closed 동작을 유지한다.
//
// 이 'active' 캐시는 폐기가 반영되기까지 최대 TTL만큼 창을 남긴다. 그 창을 좁히기 위해
// (1) 로그아웃(revokeAdminSession)이 성공하면 즉시 해당 토큰 엔트리를 무효화하고
//     (clearAdminSessionRevocationCacheEntry),
// (2) 상태 변경 요청(POST/PATCH/DELETE)은 프록시에서 이 캐시를 아예 우회해 매번
//     백엔드로 폐기 여부를 재확인한다(admin-api-proxy-route.ts) — 읽기(GET/HEAD)만
//     캐시를 사용한다.
export const ADMIN_SESSION_REVOCATION_CACHE_TTL_MS = 30_000

// 캐시 Map 무한 증가 방지 상한. 한 번 'active'로 캐시된 뒤 다시 조회되지 않는 토큰은
// TTL이 지나도 스스로 사라지지 않으므로(만료는 조회 시점에만 정리됨), set 시점에
// 만료 엔트리를 스윕하고 상한을 넘으면 가장 오래된 엔트리부터 제거한다.
export const ADMIN_SESSION_REVOCATION_CACHE_MAX_ENTRIES = 500

interface RevocationCacheEntry {
  expiresAtMs: number
}

const revocationActiveCache = new Map<string, RevocationCacheEntry>()

function readCachedActiveStatus(tokenHash: string): boolean {
  const entry = revocationActiveCache.get(tokenHash)
  if (!entry) return false
  if (entry.expiresAtMs <= Date.now()) {
    revocationActiveCache.delete(tokenHash)
    return false
  }
  return true
}

// set 직전 호출: 만료 엔트리를 경량 스윕하고, 새 키를 담아도 상한을 넘지 않도록
// 가장 오래된(삽입 순서상 앞선) 엔트리부터 제거한다.
function pruneRevocationCache(now: number, incomingKey: string): void {
  for (const [key, entry] of revocationActiveCache) {
    if (entry.expiresAtMs <= now) revocationActiveCache.delete(key)
  }
  if (revocationActiveCache.has(incomingKey)) return
  while (revocationActiveCache.size >= ADMIN_SESSION_REVOCATION_CACHE_MAX_ENTRIES) {
    const oldest = revocationActiveCache.keys().next().value
    if (oldest === undefined) break
    revocationActiveCache.delete(oldest)
  }
}

export function clearAdminSessionRevocationCache(): void {
  revocationActiveCache.clear()
}

// 특정 토큰 해시의 'active' 캐시 엔트리만 즉시 제거한다. 로그아웃 직후 같은 프로세스에서
// 캐시된 'active'가 TTL 동안 남아 폐기된 토큰이 프록시를 통과하는 것을 막는다.
export function clearAdminSessionRevocationCacheEntry(tokenHash: string): void {
  revocationActiveCache.delete(tokenHash)
}

// 관측/테스트용 — 현재 캐시에 남아 있는 엔트리 수.
export function adminSessionRevocationCacheSize(): number {
  return revocationActiveCache.size
}

export async function checkAdminSessionRevocationCached(
  options: CheckAdminSessionRevocationOptions,
): Promise<AdminSessionRevocationStatus> {
  const tokenHash = await hashSessionToken(options.sessionToken)
  if (readCachedActiveStatus(tokenHash)) {
    return 'active'
  }

  const status = await checkAdminSessionRevocation(options)
  if (status === 'active') {
    const now = Date.now()
    pruneRevocationCache(now, tokenHash)
    revocationActiveCache.set(tokenHash, {
      expiresAtMs: now + ADMIN_SESSION_REVOCATION_CACHE_TTL_MS,
    })
  } else {
    revocationActiveCache.delete(tokenHash)
  }
  return status
}

export async function revokeAdminSession({
  backendUrl,
  adminKey,
  sessionToken,
  expiresAtMs,
}: RevokeAdminSessionOptions): Promise<AdminSessionRevocationStatus> {
  const tokenHash = await hashSessionToken(sessionToken)
  try {
    const res = await fetch(adminAuthUrl(backendUrl, 'sessions/revoke'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Admin-Key': adminKey,
      },
      body: JSON.stringify({
        token_hash: tokenHash,
        expires_at: new Date(expiresAtMs).toISOString(),
      }),
      cache: 'no-store',
      signal: buildRevocationSignal(),
    })
    if (!res.ok) return 'unavailable'

    const data: unknown = await res.json()
    if (!isRecord(data) || typeof data.revoked !== 'boolean') return 'unavailable'
    if (data.revoked) {
      // 폐기 확정 즉시 이 프로세스의 'active' 캐시를 무효화 — 로그아웃 직후 같은 토큰이
      // TTL 동안 캐시된 'active'로 프록시를 통과하는 것을 막는다.
      clearAdminSessionRevocationCacheEntry(tokenHash)
      return 'revoked'
    }
    return 'active'
  } catch (error) {
    if (!isExpectedRevocationFailure(error)) throw error
    return 'unavailable'
  }
}
