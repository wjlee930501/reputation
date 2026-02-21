'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const TABS = [
  { label: '프로파일', path: 'profile' },
  { label: '콘텐츠', path: 'content' },
  { label: '스케줄', path: 'schedule' },
  { label: '리포트', path: 'reports' },
]

export default function HospitalLayout({
  children,
  params,
}: {
  children: React.ReactNode
  params: { id: string }
}) {
  const pathname = usePathname()

  return (
    <div className="flex flex-col h-full">
      {/* Tab navigation */}
      <div className="bg-white border-b border-gray-200 px-8">
        <div className="flex items-center gap-1 -mb-px">
          <Link
            href="/hospitals"
            className="mr-4 text-sm text-gray-500 hover:text-gray-700 py-4"
          >
            ← 목록
          </Link>
          {TABS.map((tab) => {
            const href = `/hospitals/${params.id}/${tab.path}`
            const isActive = pathname.startsWith(href)
            return (
              <Link
                key={tab.path}
                href={href}
                className={`px-4 py-4 text-sm font-medium border-b-2 transition-colors ${
                  isActive
                    ? 'border-blue-600 text-blue-600'
                    : 'border-transparent text-gray-500 hover:text-gray-700'
                }`}
              >
                {tab.label}
              </Link>
            )
          })}
        </div>
      </div>

      {/* Page content */}
      <div className="flex-1 overflow-auto">
        {children}
      </div>
    </div>
  )
}
