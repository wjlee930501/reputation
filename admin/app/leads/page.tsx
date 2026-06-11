'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { fetchAPI } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { SkeletonTable } from '@/app/components/Skeleton'
import { PLAN_LABELS, STATUS_LABELS, type SalesLead } from '@/types'

// backend GET /admin/leads — limit 파라미터만 지원 (기본 50, 최대 200).
// offset이 없어 "더 보기"는 limit을 키워 다시 조회하는 방식으로 동작한다.
const PAGE_SIZE = 50
const MAX_LIMIT = 200

interface HospitalCandidate {
  id: string
  name: string
  slug: string
  status: string | null
  plan: string | null
  source_lead_id: string | null
  onboarding_url: string
}

interface ConvertResponse {
  lead?: SalesLead
  hospital?: { id: string } | null
  onboarding_url?: string | null
}

type PlanOption = 'PLAN_16' | 'PLAN_12' | 'PLAN_8'

function getOnboardingHref(lead: SalesLead) {
  const params = new URLSearchParams({
    leadId: lead.id,
    name: lead.clinic_name,
    type: lead.clinic_type,
    contact: lead.contact,
  })

  if (lead.question) params.set('question', lead.question)
  if (lead.source_path) params.set('source', lead.source_path)

  return `/hospitals/new?${params.toString()}`
}

export default function LeadsPage() {
  const router = useRouter()
  const [leads, setLeads] = useState<SalesLead[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [requestedLimit, setRequestedLimit] = useState(PAGE_SIZE)

  // 전환 모달
  const [convertLead, setConvertLead] = useState<SalesLead | null>(null)
  const [candidates, setCandidates] = useState<HospitalCandidate[]>([])
  const [candidatesLoading, setCandidatesLoading] = useState(false)
  const [candidatesError, setCandidatesError] = useState<string | null>(null)
  const [selectedPlan, setSelectedPlan] = useState<PlanOption>('PLAN_16')
  const [linkHospitalId, setLinkHospitalId] = useState<string | null>(null)
  const [converting, setConverting] = useState(false)
  const [convertError, setConvertError] = useState<string | null>(null)

  // 개인정보 파기
  const [erasingLeadId, setErasingLeadId] = useState<string | null>(null)
  const [eraseError, setEraseError] = useState<string | null>(null)

  const loadLeads = useCallback(async (limit: number, options?: { append?: boolean }) => {
    if (options?.append) setLoadingMore(true)
    else setLoading(true)
    setError(null)
    try {
      const data = await fetchAPI<SalesLead[]>(`/admin/leads?limit=${limit}`)
      setLeads(Array.isArray(data) ? data : [])
      setRequestedLimit(limit)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '리드 목록을 불러오지 못했습니다.')
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }, [])

  useEffect(() => {
    void loadLeads(PAGE_SIZE)
  }, [loadLeads])

  const hasMore = leads.length >= requestedLimit && requestedLimit < MAX_LIMIT

  async function openConvertModal(lead: SalesLead) {
    if (lead.converted_hospital_id) {
      router.push(`/hospitals/${lead.converted_hospital_id}/onboarding`)
      return
    }
    setConvertLead(lead)
    setSelectedPlan('PLAN_16')
    setLinkHospitalId(null)
    setConvertError(null)
    setCandidates([])
    setCandidatesError(null)
    setCandidatesLoading(true)
    try {
      const result = await fetchAPI<{ lead_id: string; candidates: HospitalCandidate[] }>(
        `/admin/leads/${lead.id}/hospital-candidates`,
      )
      setCandidates(Array.isArray(result?.candidates) ? result.candidates : [])
    } catch (e: unknown) {
      setCandidatesError(e instanceof Error ? e.message : '중복 병원 확인에 실패했습니다.')
    } finally {
      setCandidatesLoading(false)
    }
  }

  async function handleConfirmConvert() {
    if (!convertLead || converting) return
    setConverting(true)
    setConvertError(null)
    try {
      const result = await fetchAPI<ConvertResponse>(`/admin/leads/${convertLead.id}/convert`, {
        method: 'POST',
        body: JSON.stringify({
          ...(linkHospitalId ? { hospital_id: linkHospitalId } : { plan: selectedPlan }),
          conversion_note: '상담 리드 목록에서 온보딩 시작',
        }),
      })
      const hospitalId = result?.hospital?.id ?? result?.lead?.converted_hospital_id
      if (!hospitalId) {
        throw new Error('생성된 병원 정보를 확인할 수 없습니다.')
      }
      router.push(`/hospitals/${hospitalId}/onboarding`)
    } catch (e: unknown) {
      setConvertError(e instanceof Error ? e.message : '온보딩 전환에 실패했습니다.')
      setConverting(false)
    }
  }

  async function handleErase(lead: SalesLead) {
    const confirmed = confirm(
      `${lead.clinic_name} 리드의 개인정보(연락처·문의 내용)를 즉시 파기합니다.\n파기 후에는 되돌릴 수 없습니다. 계속할까요?`,
    )
    if (!confirmed) return
    setErasingLeadId(lead.id)
    setEraseError(null)
    try {
      await fetchAPI(`/admin/leads/${lead.id}/erase`, { method: 'POST' })
      await loadLeads(requestedLimit)
    } catch (e: unknown) {
      setEraseError(e instanceof Error ? e.message : '개인정보 파기에 실패했습니다.')
    } finally {
      setErasingLeadId(null)
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
          <p className="text-xs font-medium text-slate-500">불러온 리드</p>
          <p className="mt-0.5 text-2xl font-bold text-slate-900">{leads.length}</p>
        </div>
      </div>

      {loading && <SkeletonTable rows={5} />}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          오류: {error}
        </div>
      )}

      {eraseError && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          개인정보 파기 실패: {eraseError}
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
                        onClick={() => openConvertModal(lead)}
                        className="inline-flex items-center justify-center rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-blue-700"
                      >
                        온보딩 시작
                      </button>
                    )}
                    <p className="mt-1 text-[11px] text-slate-400">
                      {lead.converted_hospital_id ? '연결 병원으로 이동' : '운영량 선택 후 병원 생성'}
                    </p>
                    <div className="mt-1 flex items-center justify-end gap-2">
                      {!lead.converted_hospital_id && (
                        <Link
                          href={getOnboardingHref(lead)}
                          className="inline-block text-[11px] font-medium text-slate-400 hover:text-slate-600 hover:underline"
                        >
                          수동 등록
                        </Link>
                      )}
                      <button
                        type="button"
                        onClick={() => handleErase(lead)}
                        disabled={erasingLeadId === lead.id}
                        className="inline-block text-[11px] font-medium text-red-400 hover:text-red-600 hover:underline disabled:opacity-50"
                      >
                        {erasingLeadId === lead.id ? '파기 중...' : '개인정보 파기'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
          {hasMore && (
            <div className="border-t border-slate-100 px-6 py-3 text-center">
              <button
                type="button"
                onClick={() => loadLeads(Math.min(requestedLimit + PAGE_SIZE, MAX_LIMIT), { append: true })}
                disabled={loadingMore}
                className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                {loadingMore ? '불러오는 중...' : '더 보기'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* 온보딩 전환 모달 */}
      {convertLead && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !converting) setConvertLead(null)
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="convert-dialog-title"
            className="w-full max-w-lg rounded-xl bg-white shadow-xl"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-slate-200 p-5">
              <div>
                <h3 id="convert-dialog-title" className="text-lg font-bold text-slate-900">
                  온보딩 전환 — {convertLead.clinic_name}
                </h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  전환하면 병원 워크스페이스가 만들어지고 온보딩 화면으로 이동합니다.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setConvertLead(null)}
                disabled={converting}
                aria-label="전환 모달 닫기"
                className="rounded-md px-2 py-1 text-xl text-slate-400 hover:bg-slate-100 hover:text-slate-600 disabled:opacity-50"
              >
                ✕
              </button>
            </div>

            <div className="space-y-4 p-5">
              {/* 중복 병원 확인 */}
              {candidatesLoading && (
                <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2.5 text-sm text-slate-500">
                  같은 이름·연락처의 기존 병원이 있는지 확인하는 중...
                </p>
              )}
              {candidatesError && (
                <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-800">
                  중복 병원 확인에 실패했습니다. 같은 병원이 이미 등록되어 있지 않은지 직접 확인해 주세요. ({candidatesError})
                </p>
              )}
              {!candidatesLoading && candidates.length > 0 && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3">
                  <p className="text-sm font-semibold text-amber-900">
                    이미 등록된 것으로 보이는 병원이 {candidates.length}곳 있습니다.
                  </p>
                  <p className="mt-1 text-xs text-amber-800">
                    중복 등록을 막으려면 아래에서 기존 병원에 연결하거나, 다른 병원이 맞는지 확인 후 새로 생성하세요.
                  </p>
                  <div className="mt-2 space-y-1.5">
                    <label className="flex items-center gap-2 rounded-md bg-white/70 px-3 py-2 text-sm text-slate-800">
                      <input
                        type="radio"
                        name="convert-target"
                        checked={linkHospitalId === null}
                        onChange={() => setLinkHospitalId(null)}
                      />
                      <span>새 병원으로 생성</span>
                    </label>
                    {candidates.map((candidate) => (
                      <label
                        key={candidate.id}
                        className="flex items-center gap-2 rounded-md bg-white/70 px-3 py-2 text-sm text-slate-800"
                      >
                        <input
                          type="radio"
                          name="convert-target"
                          checked={linkHospitalId === candidate.id}
                          onChange={() => setLinkHospitalId(candidate.id)}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="font-medium">{candidate.name}</span>
                          <span className="ml-2 font-mono text-[11px] text-slate-400">{candidate.slug}</span>
                        </span>
                        <span className="shrink-0 text-[11px] text-slate-500">
                          {candidate.status ? STATUS_LABELS[candidate.status]?.label ?? candidate.status : '-'}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
              {!candidatesLoading && !candidatesError && candidates.length === 0 && (
                <p className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2.5 text-sm text-emerald-800">
                  같은 이름·연락처로 등록된 병원이 없습니다. 새 병원으로 전환합니다.
                </p>
              )}

              {/* 요금제 선택 — 기존 병원 연결 시에는 기존 운영량 유지 */}
              {linkHospitalId === null && (
                <fieldset>
                  <legend className="text-sm font-medium text-slate-700">월간 운영량</legend>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    {(['PLAN_16', 'PLAN_12', 'PLAN_8'] as PlanOption[]).map((planOption) => (
                      <label
                        key={planOption}
                        className={`flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2.5 text-sm transition-colors ${
                          selectedPlan === planOption
                            ? 'border-blue-500 bg-blue-50 text-blue-900'
                            : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300'
                        }`}
                      >
                        <input
                          type="radio"
                          name="convert-plan"
                          value={planOption}
                          checked={selectedPlan === planOption}
                          onChange={() => setSelectedPlan(planOption)}
                        />
                        <span className="text-xs font-medium">{PLAN_LABELS[planOption]}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}

              {convertError && (
                <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700">
                  {convertError}
                </p>
              )}
            </div>

            <div className="flex gap-3 border-t border-slate-200 p-5">
              <button
                type="button"
                onClick={handleConfirmConvert}
                disabled={converting || candidatesLoading}
                className="flex-1 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {converting
                  ? '전환 중...'
                  : linkHospitalId
                    ? '기존 병원에 연결하고 온보딩 이동'
                    : '새 병원 생성하고 온보딩 이동'}
              </button>
              <button
                type="button"
                onClick={() => setConvertLead(null)}
                disabled={converting}
                className="rounded-lg bg-slate-100 px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-50"
              >
                취소
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
