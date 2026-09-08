'use client'

import { useEffect, useRef, useState } from 'react'

export interface AutofillModalProps {
  hospitalName: string
  websiteUrl: string
  blogUrl: string
  loading: boolean
  onClose: () => void
  onSubmit: (name: string, websiteUrl: string, blogUrl: string) => void
}

/** 공식 주소에서 병원 기본 정보를 한 번에 채워 넣는 입력창. 빈 칸만 채운다. */
export function AutofillModal({
  hospitalName,
  websiteUrl,
  blogUrl,
  loading,
  onClose,
  onSubmit,
}: AutofillModalProps) {
  const [name, setName] = useState(hospitalName)
  const [website, setWebsite] = useState(websiteUrl)
  const [blog, setBlog] = useState(blogUrl)
  const firstFieldRef = useRef<HTMLInputElement>(null)

  // 열 때 첫 칸으로 이동하고, 닫을 때 열었던 버튼으로 돌려준다 — 키보드만 쓰는
  // 운영자가 목록 맨 위부터 다시 내려오지 않게.
  useEffect(() => {
    const opener = document.activeElement
    firstFieldRef.current?.focus()
    return () => {
      if (opener instanceof HTMLElement) opener.focus()
    }
  }, [])

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    onSubmit(name, website, blog)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget && !loading) onClose() }}
      onKeyDown={(e) => { if (e.key === 'Escape' && !loading) onClose() }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="info-autofill-title"
        className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4 overflow-hidden"
      >
        <div className="px-6 py-5 border-b border-slate-100">
          <h3 id="info-autofill-title" className="text-base font-semibold text-slate-900">병원 정보 자동 입력</h3>
          <p className="text-xs text-slate-500 mt-1">
            홈페이지·블로그·네이버 플레이스를 확인해 병원 기본 정보를 자동으로 입력합니다.
            빈 필드만 채우며, 이미 입력된 내용은 덮어쓰지 않습니다.
            <span className="block mt-1 text-slate-400">수집에 약 20~40초가 소요될 수 있습니다.</span>
          </p>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
          <div>
            <label htmlFor="info-autofill-name" className="block text-sm font-medium text-slate-700 mb-1.5">병원명</label>
            <input
              ref={firstFieldRef}
              type="text"
              id="info-autofill-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={loading}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
            />
          </div>
          <div>
            <label htmlFor="info-autofill-website" className="block text-sm font-medium text-slate-700 mb-1.5">홈페이지 URL</label>
            <input
              type="url"
              id="info-autofill-website"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
              disabled={loading}
              placeholder="https://example.com"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
            />
          </div>
          <div>
            <label htmlFor="info-autofill-blog" className="block text-sm font-medium text-slate-700 mb-1.5">블로그 URL</label>
            <input
              type="url"
              id="info-autofill-blog"
              value={blog}
              onChange={(e) => setBlog(e.target.value)}
              disabled={loading}
              placeholder="https://blog.naver.com/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
            />
          </div>

          {loading && (
            <div className="flex items-center gap-2.5 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
              <svg className="animate-spin h-4 w-4 text-blue-600 shrink-0" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              <span className="text-sm text-blue-700">온라인 정보 수집 중…</span>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 text-sm font-medium text-slate-600 border border-slate-300 rounded-lg hover:bg-slate-50 disabled:opacity-50 transition-colors"
            >
              취소
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {loading ? '수집 중…' : '가져오기'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
