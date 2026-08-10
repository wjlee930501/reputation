'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ApiError, fetchAPI } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { SkeletonTable } from '@/app/components/Skeleton'
import { PLAN_LABELS, STATUS_LABELS, type SalesLead } from '@/types'
import { buildLeadOnboardingHref } from '@/lib/lead-onboarding'
import {
  type LeadDiagnosisSummary,
  type Tone,
  canReleaseLock,
  canRetryDelivery,
  diagnosisBadges,
  diagnosisHint,
  needsAttention,
  recoveryAction,
} from '@/lib/lead-diagnosis-status'

// backend GET /admin/leads — limit(기본 50, 최대 200) + offset 지원.
// "더 보기"는 offset append 방식이라 오래된 리드(파기 워크플로 대상 포함)까지 도달 가능.
const PAGE_SIZE = 50
// 파기 후 전체 재조회 시 백엔드 limit 상한.
const RELOAD_MAX = 200

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

type PlanOption = 'PLAN_20' | 'PLAN_16' | 'PLAN_12'

type DiagnosisAction = 'retry' | 'release' | 'remeasure' | 'rebuild'

interface ActionTarget {
  lead: SalesLead
  diagnosis: LeadDiagnosisSummary
  kind: DiagnosisAction
  idempotencyKey: string
}

const ACTION_COPY: Record<
  DiagnosisAction,
  { title: string; description: string; placeholder: string; submit: string }
> = {
  retry: {
    title: '리포트 재발송',
    description: '준비된 리포트를 같은 주소로 다시 보냅니다. 발송은 1분 안에 처리됩니다.',
    placeholder: '예) 메일 발송 설정 수정 후 재발송',
    submit: '재발송',
  },
  release: {
    title: '무료 진단 1회 제한 해제',
    description: '제3자가 먼저 신청해 원장의 기회가 소진된 경우에만 한 번 더 신청할 수 있게 합니다.',
    placeholder: '예) 대행사가 대표번호로 선점, 원장 본인 확인 완료',
    submit: '제한 해제',
  },
  remeasure: {
    title: 'AI 답변 다시 측정',
    description: '같은 환자 질문을 AI에 다시 물어 병원명이 확인되는지 측정합니다.',
    placeholder: '예) 같은 질문으로 다시 확인이 필요해 재측정',
    submit: '다시 측정',
  },
  rebuild: {
    title: '리포트 다시 만들기',
    description: '기존 리포트는 보관하고 새 리포트를 만듭니다.',
    placeholder: '예) 리포트가 열리지 않아 다시 만들기',
    submit: '리포트 다시 만들기',
  },
}

const TONE_CLASS: Record<Tone, string> = {
  ok: 'bg-emerald-50 text-emerald-700',
  progress: 'bg-blue-50 text-blue-700',
  warn: 'bg-amber-50 text-amber-800',
  danger: 'bg-red-50 text-red-700',
  muted: 'bg-slate-100 text-slate-600',
}

function getOnboardingHref(lead: SalesLead) {
  return buildLeadOnboardingHref(lead.id)
}

/** 409 본문에 담긴 진단별 거절 사유를 그대로 꺼낸다 — AE가 볼 유일한 설명이다. */
function readRefusalReasons(error: unknown): string[] {
  if (!(error instanceof ApiError)) return []
  const detail = error.detail
  if (typeof detail !== 'object' || detail === null) return []
  const rows = (detail as { diagnoses?: unknown }).diagnoses
  if (!Array.isArray(rows)) return []
  return rows
    .map((row) =>
      typeof row === 'object' && row !== null && typeof (row as { message?: unknown }).message === 'string'
        ? ((row as { message: string }).message)
        : '',
    )
    .filter(Boolean)
}

export default function LeadsPage() {
  const router = useRouter()
  const [leads, setLeads] = useState<SalesLead[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)

  // 전환 모달
  const [convertLead, setConvertLead] = useState<SalesLead | null>(null)
  const [candidates, setCandidates] = useState<HospitalCandidate[]>([])
  const [candidatesLoading, setCandidatesLoading] = useState(false)
  const [candidatesError, setCandidatesError] = useState<string | null>(null)
  const [selectedPlan, setSelectedPlan] = useState<PlanOption>('PLAN_12')
  const [linkHospitalId, setLinkHospitalId] = useState<string | null>(null)
  const [converting, setConverting] = useState(false)
  const [convertError, setConvertError] = useState<string | null>(null)

  // 개인정보 파기
  const [erasingLeadId, setErasingLeadId] = useState<string | null>(null)
  const [eraseError, setEraseError] = useState<string | null>(null)

  // 무료 진단 — 확인 필요 필터 + 재발송/잠금 해제
  const [attentionOnly, setAttentionOnly] = useState(false)
  const [actionTarget, setActionTarget] = useState<ActionTarget | null>(null)
  const [actionReason, setActionReason] = useState('')
  const [ackDuplicateRisk, setAckDuplicateRisk] = useState(false)
  const [actionSubmitting, setActionSubmitting] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionNotice, setActionNotice] = useState<string | null>(null)

  const loadLeads = useCallback(
    async (offset: number, options?: { append?: boolean; limit?: number; attention?: boolean }) => {
      if (options?.append) setLoadingMore(true)
      else setLoading(true)
      setError(null)
      const limit = options?.limit ?? PAGE_SIZE
      const attention = options?.attention ?? attentionOnly
      try {
        const query = `limit=${limit}&offset=${offset}${attention ? '&needs_attention=true' : ''}`
        const data = await fetchAPI<SalesLead[]>(`/admin/leads?${query}`)
        const page = Array.isArray(data) ? data : []
        setLeads((prev) => (options?.append ? [...prev, ...page] : page))
        setHasMore(page.length === limit)
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : '리드 목록을 불러오지 못했습니다.')
      } finally {
        setLoading(false)
        setLoadingMore(false)
      }
    },
    [attentionOnly],
  )

  useEffect(() => {
    void loadLeads(0)
  }, [loadLeads])

  useEffect(() => {
    const active = leads.some((lead) =>
      (lead.diagnoses ?? []).some((diagnosis) =>
        Object.values(diagnosis.recovery_runs ?? {}).some((run) =>
          run ? ['REQUESTED', 'QUEUED', 'RUNNING'].includes(run.state) : false,
        ),
      ),
    )
    if (!active) return
    const timer = window.setInterval(() => {
      void loadLeads(0, { limit: Math.min(Math.max(leads.length, PAGE_SIZE), RELOAD_MAX) })
    }, 5000)
    return () => window.clearInterval(timer)
  }, [leads, loadLeads])

  async function openConvertModal(lead: SalesLead) {
    if (lead.converted_hospital_id) {
      router.push(`/hospitals/${lead.converted_hospital_id}/onboarding`)
      return
    }
    setConvertLead(lead)
    setSelectedPlan('PLAN_12')
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
    if (!linkHospitalId) {
      router.push(getOnboardingHref(convertLead))
      return
    }
    setConverting(true)
    setConvertError(null)
    try {
      const result = await fetchAPI<ConvertResponse>(`/admin/leads/${convertLead.id}/convert`, {
        method: 'POST',
        body: JSON.stringify({
          hospital_id: linkHospitalId,
          plan: selectedPlan,
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
      // 현재 로드된 창 전체를 다시 읽어 파기 결과를 반영한다 (백엔드 limit 상한 내).
      await loadLeads(0, { limit: Math.min(Math.max(leads.length, PAGE_SIZE), RELOAD_MAX) })
    } catch (e: unknown) {
      setEraseError(e instanceof Error ? e.message : '개인정보 파기에 실패했습니다.')
    } finally {
      setErasingLeadId(null)
    }
  }

  function openAction(lead: SalesLead, diagnosis: LeadDiagnosisSummary, kind: DiagnosisAction) {
    setActionTarget({ lead, diagnosis, kind, idempotencyKey: crypto.randomUUID() })
    setActionReason('')
    setAckDuplicateRisk(false)
    setActionError(null)
    setActionNotice(null)
  }

  async function handleSubmitAction() {
    if (!actionTarget || actionSubmitting) return
    const reason = actionReason.trim()
    if (reason.length < 3) {
      setActionError('사유를 3자 이상 입력해 주세요. 감사 로그에 남습니다.')
      return
    }
    setActionSubmitting(true)
    setActionError(null)
    const { lead, diagnosis, kind, idempotencyKey } = actionTarget
    const path = (() => {
      switch (kind) {
        case 'retry':
          return `/admin/leads/${lead.id}/retry-report-delivery`
        case 'release':
          return `/admin/leads/${lead.id}/release-lock`
        case 'remeasure':
          return `/admin/leads/${lead.id}/diagnoses/${diagnosis.id}/remeasure`
        case 'rebuild':
          return `/admin/leads/${lead.id}/diagnoses/${diagnosis.id}/rebuild-report`
      }
    })()
    const body = kind === 'retry' ? { reason, acknowledge_duplicate_risk: ackDuplicateRisk } : { reason }
    try {
      await fetchAPI(path, {
        method: 'POST',
        headers:
          kind === 'remeasure' || kind === 'rebuild'
            ? { 'Idempotency-Key': idempotencyKey }
            : undefined,
        body: JSON.stringify(body),
      })
      setActionTarget(null)
      const notices: Record<DiagnosisAction, string> = {
        retry: '재발송을 예약했습니다. 1분 안에 발송됩니다.',
        release: '무료 진단 1회 제한을 해제했습니다.',
        remeasure: '다시 측정하도록 접수했습니다. 진행 상태를 이 화면에서 확인할 수 있습니다.',
        rebuild: '새 리포트를 만들도록 접수했습니다. 기존 리포트와 전달 이력은 유지됩니다.',
      }
      setActionNotice(notices[kind])
      await loadLeads(0, { limit: Math.min(Math.max(leads.length, PAGE_SIZE), RELOAD_MAX) })
    } catch (e: unknown) {
      const reasons = readRefusalReasons(e)
      setActionError(
        reasons.length > 0
          ? reasons.join('\n')
          : e instanceof Error
            ? e.message
            : '처리에 실패했습니다.',
      )
    } finally {
      setActionSubmitting(false)
    }
  }

  return (
    <div className="p-4 sm:p-6 lg:p-8">
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">상담 리드</h1>
          <p className="mt-1 text-sm text-slate-500">
            공개 페이지에서 접수된 병원 문의와 무료 AI 노출 진단 신청을 확인합니다.
          </p>
        </div>
        <div className="flex items-end gap-3">
          <button
            type="button"
            onClick={() => {
              const next = !attentionOnly
              setAttentionOnly(next)
              void loadLeads(0, { attention: next })
            }}
            aria-pressed={attentionOnly}
            className={`rounded-xl border px-4 py-3 text-xs font-semibold shadow-sm transition-colors ${
              attentionOnly
                ? 'border-red-300 bg-red-50 text-red-700'
                : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
            }`}
          >
            {attentionOnly ? '확인 필요만 보는 중' : '확인 필요만 보기'}
          </button>
          <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-right shadow-sm">
            <p className="text-xs font-medium text-slate-500">불러온 리드</p>
            <p className="mt-0.5 text-2xl font-bold text-slate-900">{leads.length}</p>
          </div>
        </div>
      </div>

      {actionNotice && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
          {actionNotice}
        </div>
      )}

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
          <div className="admin-responsive-table-wrap overflow-x-auto">
          <table className="admin-responsive-table w-full min-w-0 text-sm lg:min-w-[860px]">
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                <th className="px-6 py-3 text-left font-medium text-slate-600">접수 시각</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">병원</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600 sm:hidden lg:table-cell">연락처</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600 sm:hidden lg:table-cell">문의</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600 sm:hidden lg:table-cell">유입</th>
                <th className="px-6 py-3 text-left font-medium text-slate-600">무료 진단</th>
                <th className="px-6 py-3 text-right font-medium text-slate-600">다음 액션</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {leads.map((lead) => (
                <tr key={lead.id} className="transition-colors hover:bg-slate-50">
                  <td className="px-6 py-4 text-xs text-slate-500" data-label="접수 시각">{formatDateTime(lead.created_at)}</td>
                  <td className="px-6 py-4" data-primary="true">
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
                  <td className="px-6 py-4 font-medium text-slate-700 sm:hidden lg:table-cell" data-label="연락처">{lead.contact}</td>
                  <td className="px-6 py-4 text-slate-600 sm:hidden lg:table-cell" data-label="문의">
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
                  <td className="px-6 py-4 text-xs text-slate-500 sm:hidden lg:table-cell" data-label="유입">{lead.source_path ?? '-'}</td>
                  <td className="px-6 py-4" data-label="무료 진단">
                    {(lead.diagnoses ?? []).length === 0 ? (
                      <span className="text-xs text-slate-400">해당 없음</span>
                    ) : (
                      <div className="space-y-3">
                        {(lead.diagnoses ?? []).map((diagnosis) => (
                          <div key={diagnosis.id} className="min-w-0">
                            <div className="flex flex-wrap items-center gap-1.5">
                              {diagnosisBadges(diagnosis).map((badge) => (
                                <span
                                  key={badge.axis}
                                  className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${TONE_CLASS[badge.tone]}`}
                                >
                                  {badge.axis} {badge.label}
                                </span>
                              ))}
                            </div>
                            <p
                              className={`mt-1 text-[11px] ${
                                needsAttention(diagnosis)
                                  ? 'font-semibold text-red-600'
                                  : 'text-slate-500'
                              }`}
                            >
                              {diagnosisHint(diagnosis)}
                            </p>
                            <p className="mt-0.5 text-[11px] text-slate-400">
                              {diagnosis.slot_date ?? '-'}
                              {diagnosis.slot_no ? ` · ${diagnosis.slot_no}번째 자리` : ''}
                              {diagnosis.lock_released_at
                                ? ` · 잠금 해제됨(${diagnosis.lock_released_by ?? '-'})`
                                : ''}
                            </p>
                            {(() => {
                              const recovery = recoveryAction(diagnosis)
                              if (recovery?.enabled) {
                                return (
                                  <div className="mt-2 rounded-lg border border-blue-200 bg-blue-50 p-3">
                                    <p className="text-xs font-semibold text-slate-900">
                                      지금 할 일 · {recovery.label}
                                    </p>
                                    <p className="mt-1 text-xs leading-5 text-slate-600">
                                      {recovery.description}
                                    </p>
                                    {recovery.previousRun && (
                                      <p className="mt-1 text-[11px] font-medium text-red-700">
                                        이전 복구 요청도 완료되지 않았습니다. 원인을 확인한 뒤 다시 실행해 주세요.
                                      </p>
                                    )}
                                    <button
                                      type="button"
                                      onClick={() => openAction(lead, diagnosis, recovery.kind)}
                                      className="mt-2 inline-flex min-h-11 items-center rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700"
                                    >
                                      {recovery.label}
                                    </button>
                                  </div>
                                )
                              }
                              if (recovery?.kind === 'progress') {
                                return (
                                  <div className="mt-2 rounded-lg border border-blue-200 bg-blue-50 p-3">
                                    <p className="text-xs font-semibold text-blue-800">복구 작업 진행 중</p>
                                    <p className="mt-1 text-xs leading-5 text-slate-600">
                                      신청자에게는 아직 리포트가 전달되지 않았습니다. 완료 여부를 자동으로 확인하고 있습니다.
                                    </p>
                                    <Link href="/operations" className="mt-2 inline-block text-xs font-semibold text-blue-700 hover:underline">
                                      운영센터에서 상세 확인
                                    </Link>
                                  </div>
                                )
                              }
                              if (recovery?.kind === 'support') {
                                const supportInfo = [
                                  `진단 ${diagnosis.id}`,
                                  recovery.run ? `작업 ${recovery.run.id}` : null,
                                  recovery.run?.safe_error_code ? `분류 ${recovery.run.safe_error_code}` : null,
                                ].filter(Boolean).join(' · ')
                                return (
                                  <div className="mt-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
                                    <p className="text-xs font-semibold text-amber-900">{recovery.label}</p>
                                    <p className="mt-1 text-xs leading-5 text-amber-900">{recovery.description}</p>
                                    <div className="mt-2 flex flex-wrap gap-2">
                                      <button
                                        type="button"
                                        onClick={() => {
                                          void navigator.clipboard.writeText(supportInfo).then(
                                            () => setActionNotice('개발팀 문의 정보를 복사했습니다.'),
                                            () => setActionNotice('복사하지 못했습니다. 운영센터에서 진단 번호를 확인해 주세요.'),
                                          )
                                        }}
                                        className="min-h-11 rounded-lg border border-amber-300 bg-white px-3 py-2 text-xs font-semibold text-amber-900"
                                      >
                                        문의 정보 복사
                                      </button>
                                      <Link href="/operations" className="inline-flex min-h-11 items-center px-2 text-xs font-semibold text-amber-900 hover:underline">
                                        운영센터 상세
                                      </Link>
                                    </div>
                                  </div>
                                )
                              }
                              return (
                                <div className="mt-1 flex flex-wrap items-center gap-2">
                                  {canRetryDelivery(diagnosis) && (
                                    <button
                                      type="button"
                                      onClick={() => openAction(lead, diagnosis, 'retry')}
                                      className="text-[11px] font-semibold text-blue-600 hover:underline"
                                    >
                                      리포트 재발송
                                    </button>
                                  )}
                                  {canReleaseLock(diagnosis) && (
                                    <button
                                      type="button"
                                      onClick={() => openAction(lead, diagnosis, 'release')}
                                      className="text-[11px] font-medium text-slate-500 hover:text-slate-700 hover:underline"
                                    >
                                      1회 제한 해제
                                    </button>
                                  )}
                                </div>
                              )
                            })()}
                          </div>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-6 py-4 text-right" data-label="다음 액션">
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
                onClick={() => loadLeads(leads.length, { append: true })}
                disabled={loadingMore}
                className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                {loadingMore ? '불러오는 중...' : '더 보기'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* 무료 진단 액션 모달 — 재발송 / 1회 제한 해제 */}
      {actionTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !actionSubmitting) setActionTarget(null)
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="diagnosis-action-title"
            className="w-full max-w-md rounded-xl bg-white shadow-xl"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="border-b border-slate-200 p-5">
              <h3 id="diagnosis-action-title" className="text-lg font-bold text-slate-900">
                {ACTION_COPY[actionTarget.kind].title}
                {' — '}
                {actionTarget.lead.clinic_name}
              </h3>
              <p className="mt-1 text-xs text-slate-500">
                {ACTION_COPY[actionTarget.kind].description}
              </p>
            </div>

            <div className="space-y-4 p-5">
              <label className="block">
                <span className="text-sm font-medium text-slate-700">사유 (감사 로그에 기록)</span>
                <textarea
                  value={actionReason}
                  onChange={(event) => setActionReason(event.target.value)}
                  rows={3}
                  maxLength={200}
                  placeholder={
                    ACTION_COPY[actionTarget.kind].placeholder
                  }
                  className="mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:border-blue-500 focus:outline-none"
                />
              </label>

              {actionTarget.kind === 'retry' && (
                <label className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
                  <input
                    type="checkbox"
                    checked={ackDuplicateRisk}
                    onChange={(event) => setAckDuplicateRisk(event.target.checked)}
                    className="mt-0.5"
                  />
                  <span className="text-xs text-amber-900">
                    첫 발송으로부터 24시간이 지난 건이라면, 원래 메일이 실제로 나갔는지 확인할 수
                    없어 신청자가 같은 메일을 두 번 받을 수 있습니다. 확인했고 감수합니다.
                    <span className="mt-1 block text-amber-700">
                      24시간이 지나지 않은 건은 이 체크 없이도 중복 없이 재발송됩니다.
                    </span>
                  </span>
                </label>
              )}

              {actionError && (
                <p className="whitespace-pre-line rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-700">
                  {actionError}
                </p>
              )}
            </div>

            <div className="flex gap-3 border-t border-slate-200 p-5">
              <button
                type="button"
                onClick={handleSubmitAction}
                disabled={actionSubmitting}
                className="flex-1 min-h-11 rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {actionSubmitting
                  ? '처리 중...'
                  : ACTION_COPY[actionTarget.kind].submit}
              </button>
              <button
                type="button"
                onClick={() => setActionTarget(null)}
                disabled={actionSubmitting}
                className="min-h-11 rounded-lg bg-slate-100 px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-50"
              >
                취소
              </button>
            </div>
          </div>
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
                    {(['PLAN_12', 'PLAN_16', 'PLAN_20'] as PlanOption[]).map((planOption) => (
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
                    : '담당자·계약·인수 처리 기한 입력'}
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
