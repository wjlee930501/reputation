'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { fetchAPI } from '@/lib/api'
import { Hospital, STATUS_LABELS, PLAN_LABELS } from '@/types'

export default function HospitalsPage() {
  const [hospitals, setHospitals] = useState<Hospital[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchAPI('/admin/hospitals')
      .then(setHospitals)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">병원 목록</h1>
        <Link
          href="/hospitals/new"
          className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 transition-colors"
        >
          + 신규 등록
        </Link>
      </div>

      {loading && (
        <div className="text-center py-16 text-gray-500">불러오는 중...</div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">
          오류: {error}
        </div>
      )}

      {!loading && !error && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">병원명</th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">상태</th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">요금제</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">프로파일</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">사이트</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">스케줄</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {hospitals.length === 0 && (
                <tr>
                  <td colSpan={6} className="text-center py-12 text-gray-400">
                    등록된 병원이 없습니다.
                  </td>
                </tr>
              )}
              {hospitals.map((h) => {
                const status = STATUS_LABELS[h.status] ?? { label: h.status, color: 'bg-gray-100 text-gray-700' }
                return (
                  <tr key={h.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-6 py-4">
                      <Link
                        href={`/hospitals/${h.id}/profile`}
                        className="font-medium text-blue-600 hover:underline"
                      >
                        {h.name}
                      </Link>
                    </td>
                    <td className="px-6 py-4">
                      <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${status.color}`}>
                        {status.label}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-gray-600">
                      {h.plan ? (PLAN_LABELS[h.plan] ?? h.plan) : '-'}
                    </td>
                    <td className="px-6 py-4 text-center">
                      {h.profile_complete ? '✅' : '⬜'}
                    </td>
                    <td className="px-6 py-4 text-center">
                      {h.site_live ? '✅' : '⬜'}
                    </td>
                    <td className="px-6 py-4 text-center">
                      {h.schedule_set ? '✅' : '⬜'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
