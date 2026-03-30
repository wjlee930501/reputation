import { NextRequest, NextResponse } from 'next/server'
import { createHmac, randomBytes, timingSafeEqual } from 'crypto'

function generateSessionToken(secret: string): string {
  const nonce = randomBytes(16).toString('hex')
  const sig = createHmac('sha256', secret).update(nonce).digest('hex')
  return `${nonce}.${sig}`
}

function verifySessionToken(token: string, secret: string): boolean {
  const parts = token.split('.')
  if (parts.length !== 2) return false
  const [nonce, sig] = parts
  const expected = createHmac('sha256', secret).update(nonce).digest('hex')
  try {
    return timingSafeEqual(Buffer.from(sig, 'hex'), Buffer.from(expected, 'hex'))
  } catch {
    return false
  }
}

export { verifySessionToken }

export async function POST(req: NextRequest) {
  const { password } = await req.json()
  const secret = process.env.ADMIN_SESSION_SECRET

  if (!secret) {
    return NextResponse.json({ error: 'Server misconfigured' }, { status: 500 })
  }

  // Timing-safe comparison for password
  const pwBuf = Buffer.from(password)
  const secretBuf = Buffer.from(secret)
  if (pwBuf.length !== secretBuf.length || !timingSafeEqual(pwBuf, secretBuf)) {
    return NextResponse.json({ error: 'Invalid password' }, { status: 401 })
  }

  const token = generateSessionToken(secret)

  const res = NextResponse.json({ ok: true })
  res.cookies.set('admin_session', token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax',
    path: '/',
    maxAge: 60 * 60 * 24 * 7, // 7 days
  })
  return res
}
