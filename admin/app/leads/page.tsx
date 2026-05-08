'use client'

import { useEffect, useState } from 'react'
import { fetchAPI } from '@/lib/api'
import type { SalesLead } from '@/types'

function formatDateTime(value: string | null) {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleString('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export default function LeadsPage() {
  const [leads, setLeads] = useState<SalesLead[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchAPI('/admin/leads')
      .then((data) => setLeads(Array.isArray(data) ? data : []))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="p-8">
      <div className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">상담 리드</h1>
          <p className="mt-1 text-sm text-slate-500">
            공개 페이지에서 접수된 병원 문의를 확인합니다.
          </p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-right shadow-sm">
          <p className="text-xs font-medium text-slate-500">누적 리드</p>
          <p className="mt-0.5 text-2xl font-bold text-slate-900">{leads.length}</p>
        </div>
      </div>

      {loading && (
        <div className="rounded-xl border border-slate-200 bg-white py-16 text-center text-sm text-slate-500">
          리드를 불러오는 중입니다.
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          오류: {error}
        </div>
      )}

      {!loading && !error && leads.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
          <p className="text-base font-semibold text-slate-700">아직 접수된 리드가 없습니다.</p>
          <p className="mt-2 text-sm text-slate-500">
            공개 페이지 문의 폼으로 들어온 상담 요청이 이곳에 쌓입니다.
          </p>
        </div>
      )}

      {!loading && !error && leads.length > 0 && (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                <th className="px-6 py-3 text-left font-medium text-slate-600">접수 시각</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">병원</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">연락처</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">문의</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">유입</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {leads.map((lead) => (
                <tr key={lead.id} className="transition-colors hover:bg-slate-50">
                  <td className="px-6 py-4 text-xs text-slate-500">{formatDateTime(lead.created_at)}</td>
                  <td className="px-6 py-4">
                    <p className="font-semibold text-slate-900">{lead.clinic_name}</p>
                    <p className="mt-0.5 text-xs text-slate-500">{lead.clinic_type}</p>
                  </td>
                  <td className="px-6 py-4 font-medium text-slate-700">{lead.contact}</td>
                  <td className="px-6 py-4 text-slate-600">{lead.question}</td>
                  <td className="px-6 py-4 text-xs text-slate-500">{lead.source_path ?? '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
