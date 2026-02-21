'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { fetchAPI } from '@/lib/api'

interface Report {
  id: string
  hospital_id: string
  period_year: number
  period_month: number
  report_type: string
  pdf_path: string | null
  created_at: string
  sent_at: string | null
  sov_summary?: Record<string, unknown> | null
  content_summary?: Record<string, unknown> | null
}

const TYPE_LABELS: Record<string, string> = {
  V0: 'V0 진단', MONTHLY: '월간 리포트',
}

export default function ReportsPage() {
  const { id } = useParams<{ id: string }>()
  const [reports, setReports] = useState<Report[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Report | null>(null)

  useEffect(() => {
    fetchAPI(`/admin/hospitals/${id}/reports`)
      .then(setReports)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  async function openDetail(report: Report) {
    try {
      const full = await fetchAPI(`/admin/hospitals/${id}/reports/${report.id}`)
      setSelected(full)
    } catch {
      setSelected(report)
    }
  }

  return (
    <div className="p-8">
      <h2 className="text-xl font-bold text-gray-900 mb-6">리포트</h2>

      {loading && <div className="text-center py-16 text-gray-500">불러오는 중...</div>}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">오류: {error}</div>
      )}

      {!loading && !error && (
        <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">유형</th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">기간</th>
                <th className="text-left px-6 py-3 text-gray-600 font-medium">생성일</th>
                <th className="text-center px-6 py-3 text-gray-600 font-medium">PDF</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {reports.length === 0 && (
                <tr>
                  <td colSpan={4} className="text-center py-12 text-gray-400">
                    생성된 리포트가 없습니다.
                  </td>
                </tr>
              )}
              {reports.map((r) => (
                <tr key={r.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-6 py-4">
                    <button onClick={() => openDetail(r)} className="text-blue-600 hover:underline font-medium">
                      {TYPE_LABELS[r.report_type] ?? r.report_type}
                    </button>
                  </td>
                  <td className="px-6 py-4 text-gray-600">
                    {r.period_year}년 {r.period_month}월
                  </td>
                  <td className="px-6 py-4 text-gray-600">
                    {new Date(r.created_at).toLocaleDateString('ko-KR')}
                  </td>
                  <td className="px-6 py-4 text-center">
                    {r.pdf_path ? (
                      <a
                        href={r.pdf_path}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="px-3 py-1 bg-blue-100 text-blue-700 text-xs rounded hover:bg-blue-200"
                      >
                        다운로드
                      </a>
                    ) : (
                      <span className="text-gray-400 text-xs">생성 중</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Detail Modal */}
      {selected && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-lg w-full">
            <div className="flex items-center justify-between p-6 border-b border-gray-200">
              <h3 className="text-lg font-bold text-gray-900">
                {TYPE_LABELS[selected.report_type] ?? selected.report_type} — {selected.period_year}년 {selected.period_month}월
              </h3>
              <button onClick={() => setSelected(null)} className="text-gray-400 hover:text-gray-600 text-xl">✕</button>
            </div>
            <div className="p-6 space-y-4">
              {selected.sov_summary && (
                <div className="bg-blue-50 rounded-lg p-4">
                  <p className="text-xs font-medium text-blue-700 mb-2 uppercase">SoV 요약</p>
                  {Object.entries(selected.sov_summary).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-sm">
                      <span className="text-gray-600">{k}</span>
                      <span className="font-medium">{String(v)}</span>
                    </div>
                  ))}
                </div>
              )}
              {selected.content_summary && (
                <div className="bg-gray-50 rounded-lg p-4">
                  <p className="text-xs font-medium text-gray-700 mb-2 uppercase">콘텐츠 요약</p>
                  {Object.entries(selected.content_summary).map(([k, v]) => (
                    <div key={k} className="flex justify-between text-sm">
                      <span className="text-gray-600">{k}</span>
                      <span className="font-medium">{String(v)}</span>
                    </div>
                  ))}
                </div>
              )}
              {selected.pdf_path && (
                <a
                  href={selected.pdf_path}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block w-full text-center py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700"
                >
                  PDF 다운로드
                </a>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
