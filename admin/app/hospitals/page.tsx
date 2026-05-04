'use client'

import { useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { fetchAPI } from '@/lib/api'
import { Hospital, STATUS_LABELS, PLAN_LABELS } from '@/types'

export default function HospitalsPage() {
  const [hospitals, setHospitals] = useState<Hospital[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')

  useEffect(() => {
    fetchAPI('/admin/hospitals')
      .then(setHospitals)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    if (!query.trim()) return hospitals
    const q = query.trim().toLowerCase()
    return hospitals.filter(
      (h) => h.name.toLowerCase().includes(q) || h.slug.toLowerCase().includes(q),
    )
  }, [hospitals, query])

  const stats = useMemo(() => {
    const active = hospitals.filter((h) => h.status === 'ACTIVE').length
    const onboarding = hospitals.filter((h) =>
      ['ONBOARDING', 'ANALYZING', 'BUILDING', 'PENDING_DOMAIN'].includes(h.status),
    ).length
    return { total: hospitals.length, active, onboarding }
  }, [hospitals])

  return (
    <div className="p-8">
      {/* Page header */}
      <div className="mb-6">
        <div className="flex items-start justify-between gap-4 mb-2">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">병원 목록</h1>
            <p className="text-sm text-slate-500 mt-1">
              MotionLabs가 운영 중인 병원 워크스페이스 — 클릭하여 대시보드로 이동합니다.
            </p>
          </div>
          <Link
            href="/hospitals/new"
            className="inline-flex items-center gap-1.5 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors shadow-sm"
          >
            <span aria-hidden>＋</span>
            신규 병원 온보딩
          </Link>
        </div>

        {/* Quick stats */}
        {!loading && !error && hospitals.length > 0 && (
          <div className="flex items-center gap-6 text-xs text-slate-500 mt-4">
            <StatPill label="전체" value={stats.total} />
            <StatPill label="운영중" value={stats.active} tone="good" />
            <StatPill label="온보딩" value={stats.onboarding} tone="warn" />
          </div>
        )}
      </div>

      {loading && (
        <div className="bg-white border border-slate-200 rounded-xl py-16 text-center text-slate-500">
          불러오는 중...
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">
          오류: {error}
        </div>
      )}

      {!loading && !error && hospitals.length === 0 && (
        <div className="bg-white border border-dashed border-slate-300 rounded-xl py-16 px-6 text-center">
          <p className="text-base font-semibold text-slate-700">아직 등록된 병원이 없습니다.</p>
          <p className="text-sm text-slate-500 mt-2 max-w-md mx-auto">
            계약이 체결된 병원을 등록하면 초기 진단 리포트 → 병원 정보 허브 준비 → 콘텐츠 자동 생성 순서로 진행됩니다.
          </p>
          <Link
            href="/hospitals/new"
            className="inline-flex items-center gap-1.5 mt-5 px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
          >
            첫 병원 온보딩 시작
          </Link>
        </div>
      )}

      {!loading && !error && hospitals.length > 0 && (
        <>
          {/* Search */}
          <div className="mb-3 flex items-center gap-3">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="병원명 또는 slug 검색"
              className="w-72 px-3 py-2 border border-slate-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            {query && (
              <span className="text-xs text-slate-500">
                {filtered.length}개 일치
              </span>
            )}
          </div>

          <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">병원</th>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">상태</th>
                  <th className="text-left px-6 py-3 text-slate-600 font-medium">요금제</th>
                  <th className="text-center px-4 py-3 text-slate-600 font-medium">프로파일</th>
                  <th className="text-center px-4 py-3 text-slate-600 font-medium">병원 정보 허브</th>
                  <th className="text-center px-4 py-3 text-slate-600 font-medium">스케줄</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={7} className="text-center py-12 text-slate-400">
                      검색 결과가 없습니다.
                    </td>
                  </tr>
                )}
                {filtered.map((h) => {
                  const status =
                    STATUS_LABELS[h.status] ?? { label: h.status, color: 'bg-gray-100 text-gray-700' }
                  return (
                    <tr key={h.id} className="hover:bg-slate-50/80 transition-colors">
                      <td className="px-6 py-4">
                        <Link
                          href={`/hospitals/${h.id}/dashboard`}
                          className="block group"
                        >
                          <div className="font-medium text-slate-900 group-hover:text-blue-700">
                            {h.name}
                          </div>
                          <div className="text-[11px] text-slate-400 font-mono mt-0.5">
                            {h.slug}
                          </div>
                        </Link>
                      </td>
                      <td className="px-6 py-4">
                        <span
                          className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${status.color}`}
                        >
                          {status.label}
                        </span>
                      </td>
                      <td className="px-6 py-4 text-slate-600">
                        {h.plan ? PLAN_LABELS[h.plan] ?? h.plan : '-'}
                      </td>
                      <td className="px-4 py-4 text-center">
                        <CheckCell done={h.profile_complete} />
                      </td>
                      <td className="px-4 py-4 text-center">
                        <CheckCell done={h.site_live} />
                      </td>
                      <td className="px-4 py-4 text-center">
                        <CheckCell done={h.schedule_set} />
                      </td>
                      <td className="px-4 py-4 text-right">
                        <Link
                          href={`/hospitals/${h.id}/dashboard`}
                          className="text-xs font-medium text-blue-600 hover:text-blue-800 hover:underline"
                        >
                          열기 →
                        </Link>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

function StatPill({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone?: 'good' | 'warn'
}) {
  const dot =
    tone === 'good' ? 'bg-emerald-500' : tone === 'warn' ? 'bg-amber-500' : 'bg-slate-400'
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`w-1.5 h-1.5 rounded-full ${dot}`} aria-hidden />
      <span className="text-slate-500">{label}</span>
      <span className="font-semibold text-slate-700">{value}</span>
    </span>
  )
}

function CheckCell({ done }: { done: boolean | undefined }) {
  if (done) {
    return (
      <span
        className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-emerald-100 text-emerald-700 text-xs font-bold"
        title="완료"
        aria-label="완료"
      >
        ✓
      </span>
    )
  }
  return (
    <span
      className="inline-block w-5 h-5 rounded-full border border-dashed border-slate-300"
      title="미완료"
      aria-label="미완료"
    />
  )
}
