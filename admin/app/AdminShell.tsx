'use client'

import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import { useState } from 'react'

import { buildAdminCsrfHeaders } from '@/lib/csrf'
import { clearPublisherIdentity } from '@/lib/publisher-identity'

const NAV_ITEMS = [
  {
    href: '/hospitals',
    label: '병원 목록',
    meta: '관리',
    icon: (
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
        <path d="M3 7v10a1 1 0 0 0 1 1h4V13h4v5h4a1 1 0 0 0 1-1V7l-7-4z" />
        <path d="M8 3v4" />
        <path d="M12 3v4" />
        <path d="M3 7h14" />
      </svg>
    ),
  },
  {
    href: '/hospitals/new',
    label: '신규 병원 온보딩',
    icon: (
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
        <path d="M10 4v12M4 10h12" />
      </svg>
    ),
  },
  {
    href: '/leads',
    label: '상담 리드',
    icon: (
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
        <path d="M3 5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H7l-4 4V5z" />
      </svg>
    ),
  },
  {
    href: '/operations',
    label: '비용 사용량',
    meta: '설정',
    icon: (
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
        <path d="M3 16V9M8 16V5M13 16v-4M18 16V7" />
      </svg>
    ),
  },
  {
    href: '/accounts',
    label: '운영자 계정',
    meta: '설정',
    icon: (
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
        <path d="M10 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6z" />
        <path d="M4 17a6 6 0 0 1 12 0" />
      </svg>
    ),
  },
]

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const router = useRouter()
  const [logoutError, setLogoutError] = useState(false)
  const isLogin = pathname === '/login'

  async function handleLogout() {
    setLogoutError(false)
    const res = await fetch('/api/auth/logout', { method: 'POST', headers: buildAdminCsrfHeaders('POST') })
    if (!res.ok) {
      setLogoutError(true)
      return
    }
    // 공용 PC에서 다음 로그인 전까지 이전 AE의 발행 담당자 이름이 남지 않게 한다.
    // (신원·역할은 세션 쿠키에서 읽으므로 쿠키 삭제로 함께 사라진다.)
    clearPublisherIdentity()
    router.push('/login')
  }

  if (isLogin) {
    return <div className="min-h-screen bg-slate-50">{children}</div>
  }

  return (
    <div className="min-h-screen bg-slate-50 lg:flex lg:h-screen">
      <aside className="sticky top-0 z-50 flex shrink-0 flex-col bg-slate-900 text-slate-100 lg:static lg:w-60">
        <div className="flex h-14 items-center border-b border-slate-800 px-4 lg:block lg:h-auto lg:px-5 lg:py-4">
          <Link href="/hospitals" className="min-w-0 flex-1 lg:block">
            <div className="flex items-baseline gap-1.5">
              <span className="text-lg font-bold tracking-tight text-white">Re:putation</span>
              <span className="text-[11px] font-semibold tracking-wider text-blue-300">운영</span>
            </div>
            <p className="mt-0.5 hidden text-[11px] text-slate-400 lg:block">MotionLabs 내부 운영 콘솔</p>
            <span className="mt-2 hidden items-center gap-1 rounded-full bg-blue-500/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-blue-200 lg:inline-flex">
              <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-blue-400" />
              Research Preview
            </span>
          </Link>

          <details className="group relative ml-auto lg:hidden">
            <summary className="inline-flex min-h-11 cursor-pointer list-none items-center gap-2 rounded-lg border border-slate-700 px-3 text-sm font-semibold text-slate-100 [&::-webkit-details-marker]:hidden">
              메뉴
              <span aria-hidden className="text-[10px] transition-transform group-open:rotate-180">▼</span>
            </summary>
            <div className="absolute right-0 top-[calc(100%+8px)] z-50 w-[min(22rem,calc(100vw-2rem))] rounded-xl border border-slate-700 bg-slate-900 p-2 shadow-xl">
              <nav className="space-y-1" aria-label="모바일 운영 메뉴">
                {NAV_ITEMS.map((item) => {
                  const active =
                    item.href === '/hospitals'
                      ? pathname === '/hospitals' || (pathname.startsWith('/hospitals/') && !pathname.startsWith('/hospitals/new'))
                      : pathname === item.href || pathname.startsWith(`${item.href}/`)
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      aria-current={active ? 'page' : undefined}
                      className={`flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm ${
                        active ? 'bg-slate-800 text-white' : 'text-slate-300 hover:bg-slate-800 hover:text-white'
                      }`}
                    >
                      <span aria-hidden className="inline-flex h-6 w-6 items-center justify-center rounded border border-slate-700">
                        {item.icon}
                      </span>
                      {item.label}
                    </Link>
                  )
                })}
              </nav>
              <button
                type="button"
                onClick={handleLogout}
                className="mt-2 flex min-h-11 w-full items-center gap-3 border-t border-slate-800 px-3 pt-2 text-sm text-slate-300 hover:text-white"
              >
                로그아웃
              </button>
              {logoutError ? <p role="alert" className="px-3 py-2 text-xs text-red-300">로그아웃에 실패했습니다.</p> : null}
            </div>
          </details>
        </div>

        <nav className="hidden gap-1 overflow-x-auto px-3 py-3 lg:flex lg:flex-1 lg:flex-col lg:gap-0.5 lg:overflow-visible lg:py-4" aria-label="운영 메뉴">
          <p className="hidden px-3 pb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500 lg:block">
            운영 메뉴
          </p>
          {NAV_ITEMS.map((item) => {
            const active =
              item.href === '/hospitals'
                ? pathname === '/hospitals' || (pathname.startsWith('/hospitals/') && !pathname.startsWith('/hospitals/new'))
                : pathname === item.href || pathname.startsWith(`${item.href}/`)
            return (
              <Link
                key={item.href}
                href={item.href}
                aria-current={active ? 'page' : undefined}
                className={`flex min-w-fit items-center justify-between gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                  active
                    ? 'bg-slate-800 text-white'
                    : 'text-slate-300 hover:bg-slate-800 hover:text-white'
                }`}
              >
                <span className="flex items-center gap-2.5">
                  <span
                    aria-hidden
                    className="inline-flex h-5 w-5 items-center justify-center rounded border border-slate-700 text-slate-300"
                  >
                    {item.icon}
                  </span>
                  {item.label}
                </span>
                {item.meta && (
                  <span className="hidden text-[11px] text-slate-500 group-hover:text-slate-300 xl:inline">
                    {item.meta}
                  </span>
                )}
              </Link>
            )
          })}

          <div className="hidden px-3 py-2 text-[11px] leading-relaxed text-slate-400 lg:mt-6 lg:block">
            병원 자료, 운영 기준 승인, 콘텐츠 자동 발행·후행 점검, 월간 리포트 순서로 진행합니다.
          </div>
        </nav>

        <div className="hidden border-t border-slate-800 px-3 py-3 lg:block lg:px-4">
          <button
            type="button"
            onClick={handleLogout}
            className="flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-800 hover:text-white"
          >
            <span
              aria-hidden
              className="inline-flex h-5 w-5 items-center justify-center rounded border border-slate-700 text-slate-300"
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className="h-4 w-4">
                <path d="M8 17H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h3" />
                <path d="M13 14l4-4-4-4" />
                <path d="M17 10H8" />
              </svg>
            </span>
            로그아웃
          </button>
          {logoutError ? (
            <p role="alert" className="mt-2 px-3 text-xs leading-relaxed text-red-300">
              로그아웃에 실패했습니다. 잠시 후 다시 시도해 주세요.
            </p>
          ) : null}
        </div>

        <div className="hidden space-y-1 border-t border-slate-800 px-4 py-3 lg:block">
          <p className="text-[11px] font-medium text-slate-300">MotionLabs Inc.</p>
          <p className="text-[10px] text-slate-500">v1.0 Research Preview · 내부 운영 콘솔</p>
          <a
            href="https://motionlabs.kr"
            target="_blank"
            rel="noopener"
            className="text-[10px] text-blue-300 transition-colors hover:text-blue-200"
          >
            motionlabs.kr ↗
          </a>
        </div>
      </aside>

      <main id="main-content" className="min-w-0 flex-1 overflow-auto bg-slate-50">{children}</main>
    </div>
  )
}
