'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { fetchAPI } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { SkeletonTable } from '@/app/components/Skeleton'
import type { SalesLead } from '@/types'

export default function LeadsPage() {
  const router = useRouter()
  const [leads, setLeads] = useState<SalesLead[]>([])
  const [convertingLeadId, setConvertingLeadId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchAPI('/admin/leads')
      .then((data) => setLeads(Array.isArray(data) ? data : []))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  async function handleConvertLead(lead: SalesLead) {
    if (lead.converted_hospital_id) {
      router.push(`/hospitals/${lead.converted_hospital_id}/onboarding`)
      return
    }

    setConvertingLeadId(lead.id)
    setError(null)
    try {
      const result = await fetchAPI(`/admin/leads/${lead.id}/convert`, {
        method: 'POST',
        body: JSON.stringify({
          plan: 'PLAN_16',
          conversion_note: '상담 리드 목록에서 온보딩 시작',
        }),
      })
      const hospitalId = result?.hospital?.id ?? result?.lead?.converted_hospital_id
      if (!hospitalId) {
        throw new Error('생성된 병원 정보를 확인할 수 없습니다.')
      }
      router.push(`/hospitals/${hospitalId}/onboarding`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '온보딩 전환에 실패했습니다.')
    } finally {
      setConvertingLeadId(null)
    }
  }

  return (
    <div className="p-4 sm:p-6 lg:p-8">
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">상담 리드</h1>
          <p className="mt-1 text-sm text-slate-500">
            공개 페이지에서 접수된 병원 문의를 확인하고 신규 병원 온보딩으로 전환합니다.
          </p>
        </div>
        <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-right shadow-sm">
          <p className="text-xs font-medium text-slate-500">누적 리드</p>
          <p className="mt-0.5 text-2xl font-bold text-slate-900">{leads.length}</p>
        </div>
      </div>

      {loading && <SkeletonTable rows={5} />}

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
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
          <table className="min-w-[860px] w-full text-sm">
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                <th className="px-6 py-3 text-left font-medium text-slate-600">접수 시각</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">병원</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">연락처</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">문의</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">유입</th>
                <th className="px-6 py-3 text-right font-medium text-slate-600">다음 액션</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {leads.map((lead) => (
                <tr key={lead.id} className="transition-colors hover:bg-slate-50">
                  <td className="px-6 py-4 text-xs text-slate-500">{formatDateTime(lead.created_at)}</td>
                  <td className="px-6 py-4">
                    <p className="font-semibold text-slate-900">{lead.clinic_name}</p>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      <span className="text-xs text-slate-500">{lead.clinic_type}</span>
                      <span
                        className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                          lead.converted_hospital_id
                            ? 'bg-emerald-50 text-emerald-700'
                            : 'bg-amber-50 text-amber-700'
                        }`}
                      >
                        {lead.converted_hospital_id ? '온보딩 전환됨' : '온보딩 대기'}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4 font-medium text-slate-700">{lead.contact}</td>
                  <td className="px-6 py-4 text-slate-600">
                    <p className="line-clamp-2 max-w-sm">{lead.question}</p>
                    <p className="mt-1 text-[11px] text-slate-400">
                      개인정보 동의 {lead.privacy ? '완료' : '미확인'}
                    </p>
                    {lead.notification_status === 'FAILED' && (
                      <p className="mt-1 text-[11px] font-semibold text-red-600">
                        알림 실패: {lead.notification_error ?? '설정을 확인해 주세요'}
                      </p>
                    )}
                    {lead.notification_status === 'SENT' && (
                      <p className="mt-1 text-[11px] font-medium text-emerald-600">운영 알림 완료</p>
                    )}
                  </td>
                  <td className="px-6 py-4 text-xs text-slate-500">{lead.source_path ?? '-'}</td>
                  <td className="px-6 py-4 text-right">
                    {lead.converted_hospital_id ? (
                      <Link
                        href={`/hospitals/${lead.converted_hospital_id}/onboarding`}
                        className="inline-flex items-center justify-center rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
                      >
                        온보딩 허브
                      </Link>
                    ) : (
                      <button
                        type="button"
                        onClick={() => handleConvertLead(lead)}
                        disabled={convertingLeadId === lead.id}
                        className="inline-flex items-center justify-center rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {convertingLeadId === lead.id ? '전환 중...' : '온보딩 시작'}
                      </button>
                    )}
                    <p className="mt-1 text-[11px] text-slate-400">
                      {lead.converted_hospital_id ? '연결 병원으로 이동' : '병원 생성 후 허브로 이동'}
                    </p>
                    {!lead.converted_hospital_id && (
                      <Link
                        href={getOnboardingHref(lead)}
                        className="mt-1 inline-block text-[11px] font-medium text-slate-400 hover:text-slate-600 hover:underline"
                      >
                        수동 등록
                      </Link>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </div>
  )
}
