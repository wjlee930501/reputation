function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer
}

function hexToBytes(value: string): Uint8Array | null {
  if (!/^[0-9a-f]+$/i.test(value) || value.length % 2 !== 0) {
    return null
  }

  const bytes = new Uint8Array(value.length / 2)
  for (let i = 0; i < value.length; i += 2) {
    bytes[i / 2] = Number.parseInt(value.slice(i, i + 2), 16)
  }
  return bytes
}

async function importSessionKey(secret: string): Promise<CryptoKey> {
  return crypto.subtle.importKey(
    'raw',
    toArrayBuffer(new TextEncoder().encode(secret)),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign', 'verify'],
  )
}

const DEFAULT_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7

export type AdminSession = {
  accountId: string
  email: string
  name: string
  role: string
  expiresAt: number
}

export type AdminSessionPayload = Omit<AdminSession, 'expiresAt'>

function encodePayload(payload: AdminSessionPayload): string {
  return bytesToHex(new TextEncoder().encode(JSON.stringify(payload)))
}

function decodePayload(value: string): AdminSessionPayload | null {
  const bytes = hexToBytes(value)
  if (!bytes) return null

  try {
    const parsed = JSON.parse(new TextDecoder().decode(bytes)) as Partial<AdminSessionPayload>
    if (
      typeof parsed.accountId !== 'string' ||
      typeof parsed.email !== 'string' ||
      typeof parsed.name !== 'string' ||
      typeof parsed.role !== 'string' ||
      !parsed.accountId ||
      !parsed.email ||
      !parsed.name ||
      !parsed.role
    ) {
      return null
    }
    return {
      accountId: parsed.accountId,
      email: parsed.email,
      name: parsed.name,
      role: parsed.role,
    }
  } catch {
    return null
  }
}

export async function generateSessionToken(
  secret: string,
  maxAgeSeconds = DEFAULT_SESSION_MAX_AGE_SECONDS,
  payload: AdminSessionPayload,
): Promise<string> {
  const nonceBytes = new Uint8Array(16)
  crypto.getRandomValues(nonceBytes)

  const nonce = bytesToHex(nonceBytes)
  const expiresAt = String(Date.now() + maxAgeSeconds * 1000)
  const encodedPayload = encodePayload(payload)
  const key = await importSessionKey(secret)
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    toArrayBuffer(new TextEncoder().encode(`${nonce}.${expiresAt}.${encodedPayload}`)),
  )

  return `${nonce}.${expiresAt}.${encodedPayload}.${bytesToHex(new Uint8Array(signature))}`
}

export async function readSessionToken(token: string, secret: string): Promise<AdminSession | null> {
  const parts = token.split('.')
  if (parts.length !== 4) {
    return null
  }

  const [nonce, expiresAt, encodedPayload, signatureHex] = parts
  const expiresAtMs = Number.parseInt(expiresAt, 10)
  if (!Number.isFinite(expiresAtMs) || expiresAtMs <= Date.now()) {
    return null
  }

  const payload = decodePayload(encodedPayload)
  if (!payload) {
    return null
  }

  const signature = hexToBytes(signatureHex)
  if (!signature) {
    return null
  }

  const key = await importSessionKey(secret)
  const isValid = await crypto.subtle.verify(
    'HMAC',
    key,
    toArrayBuffer(signature),
    toArrayBuffer(new TextEncoder().encode(`${nonce}.${expiresAt}.${encodedPayload}`)),
  )
  if (!isValid) {
    return null
  }

  return { ...payload, expiresAt: expiresAtMs }
}

export async function verifySessionToken(token: string, secret: string): Promise<boolean> {
  return Boolean(await readSessionToken(token, secret))
}
