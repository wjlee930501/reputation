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

export async function generateSessionToken(
  secret: string,
  maxAgeSeconds = DEFAULT_SESSION_MAX_AGE_SECONDS,
): Promise<string> {
  const nonceBytes = new Uint8Array(16)
  crypto.getRandomValues(nonceBytes)

  const nonce = bytesToHex(nonceBytes)
  const expiresAt = String(Date.now() + maxAgeSeconds * 1000)
  const key = await importSessionKey(secret)
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    toArrayBuffer(new TextEncoder().encode(`${nonce}.${expiresAt}`)),
  )

  return `${nonce}.${expiresAt}.${bytesToHex(new Uint8Array(signature))}`
}

export async function verifySessionToken(token: string, secret: string): Promise<boolean> {
  const parts = token.split('.')
  if (parts.length !== 3) {
    return false
  }

  const [nonce, expiresAt, signatureHex] = parts
  const expiresAtMs = Number.parseInt(expiresAt, 10)
  if (!Number.isFinite(expiresAtMs) || expiresAtMs <= Date.now()) {
    return false
  }

  const signature = hexToBytes(signatureHex)
  if (!signature) {
    return false
  }

  const key = await importSessionKey(secret)
  return crypto.subtle.verify(
    'HMAC',
    key,
    toArrayBuffer(signature),
    toArrayBuffer(new TextEncoder().encode(`${nonce}.${expiresAt}`)),
  )
}
