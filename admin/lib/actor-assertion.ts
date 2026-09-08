// 백엔드 admin API는 공개 LB로 열려 있고 인가는 공유 X-Admin-Key 하나다 — X-Admin-Actor는
// 그 키를 아는 쪽이면 누구나 위조할 수 있다(H-10). BFF가 세션을 확인한 뒤 "누가 요청했는지"를
// 서명해 함께 보내고, 백엔드는 사람 변경(쓰기)에 그 단언을 요구한다(backend/app/core/security.py).
//
// 형식: `v1.<base64url(json)>.<hex hmac-sha256>` — 서명 대상은 `v1.<base64url(json)>`.
// nonce는 어디에도 저장하지 않는다(재생 저장소 없음). 재생 창은 120초 TTL로만 제한한다.

const DEFAULT_ACTOR_ASSERTION_TTL_MS = 120_000

export type ActorAssertionClaims = {
  email: string
  role: string
}

export type ActorAssertionPayload = ActorAssertionClaims & {
  iat: number
  exp: number
  nonce: string
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

function hexToBytes(value: string): Uint8Array | null {
  if (!/^[0-9a-f]+$/i.test(value) || value.length % 2 !== 0) return null
  const bytes = new Uint8Array(value.length / 2)
  for (let i = 0; i < value.length; i += 2) {
    bytes[i / 2] = Number.parseInt(value.slice(i, i + 2), 16)
  }
  return bytes
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function fromBase64Url(value: string): Uint8Array | null {
  try {
    const padded = value.replace(/-/g, '+').replace(/_/g, '/')
    const binary = atob(padded + '='.repeat((4 - (padded.length % 4)) % 4))
    const bytes = new Uint8Array(binary.length)
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
    return bytes
  } catch {
    return null
  }
}

async function importActorKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    'raw',
    toArrayBuffer(new TextEncoder().encode(secret)),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign', 'verify'],
  )
}

export async function buildActorAssertion(
  secret: string,
  claims: ActorAssertionClaims,
  options: { ttlMs?: number } = {},
): Promise<string> {
  const nonceBytes = new Uint8Array(16)
  crypto.getRandomValues(nonceBytes)

  const issuedAt = Date.now()
  const payload: ActorAssertionPayload = {
    email: claims.email,
    role: claims.role,
    iat: issuedAt,
    exp: issuedAt + (options.ttlMs ?? DEFAULT_ACTOR_ASSERTION_TTL_MS),
    nonce: bytesToHex(nonceBytes),
  }

  const encoded = toBase64Url(new TextEncoder().encode(JSON.stringify(payload)))
  const key = await importActorKey(secret)
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    toArrayBuffer(new TextEncoder().encode(`v1.${encoded}`)),
  )
  return `v1.${encoded}.${bytesToHex(new Uint8Array(signature))}`
}

/** 백엔드 검증과 같은 규칙을 테스트에서 재현한다(런타임 경로는 서명만 한다). */
export async function parseActorAssertionForTest(
  secret: string,
  token: string,
): Promise<ActorAssertionPayload | null> {
  const parts = token.split('.')
  if (parts.length !== 3 || parts[0] !== 'v1') return null

  const [, encoded, signatureHex] = parts
  const signature = hexToBytes(signatureHex)
  if (!signature) return null

  const key = await importActorKey(secret)
  const isValid = await crypto.subtle.verify(
    'HMAC',
    key,
    toArrayBuffer(signature),
    toArrayBuffer(new TextEncoder().encode(`v1.${encoded}`)),
  )
  if (!isValid) return null

  const decoded = fromBase64Url(encoded)
  if (!decoded) return null

  try {
    const payload = JSON.parse(new TextDecoder().decode(decoded)) as Partial<ActorAssertionPayload>
    if (
      typeof payload.email !== 'string' ||
      typeof payload.role !== 'string' ||
      typeof payload.iat !== 'number' ||
      typeof payload.exp !== 'number' ||
      typeof payload.nonce !== 'string' ||
      Date.now() > payload.exp
    ) {
      return null
    }
    return payload as ActorAssertionPayload
  } catch {
    return null
  }
}
