'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { fetchAPI } from '@/lib/api'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from 'recharts'

interface TrendPoint {
  week_start: string
  sov_pct: number
  mention_count: number
  total_count: number
}

interface QueryRow {
  query_id: string
  query_text: string
  mention_rate: number
  mention_count: number
  total_count: number
  last_measured_at: string | null
}

function KPICard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-6">
      <p className="text-sm text-gray-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  )
}

export default function DashboardPage() {
  const { id } = useParams<{ id: string }>()
  const [trendData, setTrendData] = useState<TrendPoint[]>([])
  const [queries, setQueries] = useState<QueryRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetchAPI(`/admin/hospitals/${id}/sov/trend`).catch(() => [] as TrendPoint[]),
      fetchAPI(`/admin/hospitals/${id}/sov/queries`).catch(() => [] as QueryRow[]),
    ])
      .then(([trend, qs]: [TrendPoint[], QueryRow[]]) => {
        setTrendData(Array.isArray(trend) ? trend : [])
        setQueries(Array.isArray(qs) ? qs : [])
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  const lastPoint = trendData.length > 0 ? trendData[trendData.length - 1] : null
  const prevPoint = trendData.length > 1 ? trendData[trendData.length - 2] : null
  const currentSov = lastPoint?.sov_pct ?? null
  const prevSov = prevPoint?.sov_pct ?? null
  const change = currentSov !== null && prevSov !== null ? currentSov - prevSov : null
  const queryCount = queries.length

  const isEmpty = !loading && !error && trendData.length === 0

  return (
    <div className="p-8">
      <h2 className="text-xl font-bold text-gray-900 mb-6">SoV 대시보드</h2>

      {loading && (
        <div className="text-center py-16 text-gray-500">불러오는 중...</div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm mb-6">
          오류: {error}
        </div>
      )}

      {!loading && isEmpty && (
        <div className="bg-gray-50 border border-gray-200 rounded-xl p-12 text-center">
          <p className="text-gray-500 text-base font-medium">SoV 데이터가 아직 없습니다.</p>
          <p className="text-gray-400 text-sm mt-2">첫 주간 측정 후 표시됩니다.</p>
        </div>
      )}

      {!loading && !isEmpty && (
        <>
          {/* KPI Cards */}
          <div className="grid grid-cols-3 gap-6 mb-8">
            <KPICard
              label="현재 SoV"
              value={currentSov !== null ? `${currentSov.toFixed(1)}%` : '-'}
            />
            <KPICard
              label="전주 대비"
              value={
                change !== null
                  ? `${change > 0 ? '+' : ''}${change.toFixed(1)}%p`
                  : '-'
              }
              sub={prevSov !== null ? `전주: ${prevSov.toFixed(1)}%` : undefined}
            />
            <KPICard
              label="측정 쿼리"
              value={`${queryCount}개`}
            />
          </div>

          {/* Line Chart */}
          {trendData.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 p-6 mb-8">
              <h3 className="text-base font-semibold text-gray-800 mb-4">SoV 주간 추이</h3>
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={trendData} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                  <XAxis dataKey="week_start" tick={{ fontSize: 12 }} />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} unit="%" />
                  <Tooltip formatter={(value) => typeof value === 'number' ? `${value.toFixed(1)}%` : value} />
                  <Legend />
                  <Line
                    dataKey="sov_pct"
                    stroke="#1A4B8C"
                    strokeWidth={2}
                    name="SoV"
                    dot={false}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Queries Table */}
          {queries.length > 0 && (
            <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
              <div className="px-6 py-4 border-b border-gray-100">
                <h3 className="text-base font-semibold text-gray-800">쿼리별 멘션율</h3>
              </div>
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="text-left px-6 py-3 text-gray-600 font-medium">쿼리</th>
                    <th className="text-center px-6 py-3 text-gray-600 font-medium">멘션율</th>
                    <th className="text-center px-6 py-3 text-gray-600 font-medium">최근 측정</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {queries.map((q) => (
                    <tr key={q.query_id} className="hover:bg-gray-50 transition-colors">
                      <td className="px-6 py-3 text-gray-700">{q.query_text}</td>
                      <td className="px-6 py-3 text-center">
                        <span className={`font-medium ${q.mention_rate >= 50 ? 'text-green-600' : 'text-gray-500'}`}>
                          {q.mention_rate.toFixed(0)}%
                        </span>
                      </td>
                      <td className="px-6 py-3 text-center text-gray-400 text-xs">
                        {q.last_measured_at ? new Date(q.last_measured_at).toLocaleDateString('ko-KR') : '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}
