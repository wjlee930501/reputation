export class BodyTooLargeError extends Error {
  readonly maxBytes: number

  constructor(maxBytes: number) {
    super(`Request body exceeds ${maxBytes} bytes`)
    this.name = 'BodyTooLargeError'
    this.maxBytes = maxBytes
  }
}

function declaredContentLength(headers: Headers): number | null {
  const raw = headers.get('content-length')
  if (!raw) return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null
}

async function readBodyBytesWithLimit(request: Request, maxBytes: number): Promise<Uint8Array> {
  const declaredLength = declaredContentLength(request.headers)
  if (declaredLength !== null && declaredLength > maxBytes) {
    throw new BodyTooLargeError(maxBytes)
  }

  const reader = request.body?.getReader()
  if (!reader) return new Uint8Array()

  const chunks: Uint8Array[] = []
  let total = 0
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    if (!value) continue
    total += value.byteLength
    if (total > maxBytes) {
      await reader.cancel().catch(() => undefined)
      throw new BodyTooLargeError(maxBytes)
    }
    chunks.push(value)
  }

  const body = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) {
    body.set(chunk, offset)
    offset += chunk.byteLength
  }
  return body
}

export async function readJsonBodyWithLimit(request: Request, maxBytes: number): Promise<unknown> {
  const bytes = await readBodyBytesWithLimit(request, maxBytes)
  return JSON.parse(new TextDecoder().decode(bytes))
}

export async function readFormDataBodyWithLimit(request: Request, maxBytes: number): Promise<FormData> {
  const bytes = await readBodyBytesWithLimit(request, maxBytes)
  const contentType = request.headers.get('content-type') || ''
  const body = new ArrayBuffer(bytes.byteLength)
  new Uint8Array(body).set(bytes)
  return new Response(body, { headers: { 'Content-Type': contentType } }).formData()
}
