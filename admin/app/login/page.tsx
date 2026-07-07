'use client'

import { Suspense, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { storeCurrentAccountName } from '@/lib/publisher-identity'

function LoginForm() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const router = useRouter()
  const searchParams = useSearchParams()
  const hasError = Boolean(error)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError('')

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })

      if (res.ok) {
        // 발행 담당자 이름 기본값을 로그인 계정과 연동한다 — 공용 PC에서 이전 AE
        // 이름이 남지 않도록 세션 범위(sessionStorage)로만 저장한다.
        const body = await res.json().catch(() => null) as { account?: { name?: string } } | null
        if (body?.account?.name) storeCurrentAccountName(body.account.name)

        const raw = searchParams.get('redirect')
        const redirect = raw?.startsWith('/') && !raw.startsWith('//') ? raw : '/hospitals'
        router.push(redirect)
      } else if (res.status === 429) {
        setError('로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.')
      } else {
        setError('인증에 실패했습니다.')
      }
    } catch {
      setError('네트워크 오류가 발생했습니다.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main id="main-content" tabIndex={-1} className="min-h-screen flex items-center justify-center bg-slate-50">
      <form onSubmit={handleSubmit} className="bg-white p-8 rounded-xl shadow-sm w-full max-w-sm">
        <h1 className="text-xl font-bold text-slate-800 mb-6 text-center">Re:putation Admin</h1>
        <label htmlFor="admin-email" className="sr-only">관리자 이메일</label>
        <input
          id="admin-email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="관리자 이메일"
          className="w-full border border-slate-300 rounded-lg px-4 py-2 mb-3 focus:outline-none focus:ring-2 focus:ring-blue-500"
          autoComplete="email"
          aria-invalid={hasError}
          aria-describedby={hasError ? 'login-error' : undefined}
          required
          autoFocus
        />
        <label htmlFor="admin-password" className="sr-only">관리자 비밀번호</label>
        <input
          id="admin-password"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="관리자 비밀번호"
          className="w-full border border-slate-300 rounded-lg px-4 py-2 mb-4 focus:outline-none focus:ring-2 focus:ring-blue-500"
          autoComplete="current-password"
          aria-invalid={hasError}
          aria-describedby={hasError ? 'login-error' : undefined}
          required
        />
        {error && (
          <p id="login-error" role="alert" aria-live="polite" className="text-red-500 text-sm mb-4">
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={loading}
          className="w-full bg-blue-600 text-white py-2 rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
        >
          {loading ? '로그인 중...' : '로그인'}
        </button>
      </form>
    </main>
  )
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-slate-50" />}>
      <LoginForm />
    </Suspense>
  )
}
