'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ApiError, fetchAPI } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { fetchCurrentAccount, type CurrentAccount } from '@/lib/current-account'
import { readClinicNameFromLeadContext } from '@/lib/lead-onboarding'
import {
  ONBOARDING_CREATE_REQUEST_STORAGE_KEY,
  ONBOARDING_WORKFLOW_STORAGE_KEY,
  acceptanceDecision,
  acceptancePayload,
  contractPayload,
  defaultAcquisitionDates,
  handoffNextAction,
  koreanDateInputValue,
  koreanDateTimeLocalInputValue,
  parseOnboardingCreateRequestId,
  parseOnboardingWorkflowCheckpoint,
  parsePlanCode,
  serializeOnboardingWorkflowCheckpoint,
} from '@/lib/handoff'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import { PLAN_CONTRACT_LABELS, type AdminAccountSummary, type Handoff, type PlanCode } from '@/types'

interface LeadContext {
  id: string | null
}

interface DuplicateCandidate {
  id: string
  name: string
  status: string
  onboarding_url: string
}

export default function NewHospitalPage() {
  const router = useRouter()
  const [defaultDates] = useState(() => defaultAcquisitionDates())
  const [name, setName] = useState('')
  const [plan, setPlan] = useState<PlanCode>('PLAN_12')
  const [leadContext, setLeadContext] = useState<LeadContext | null>(null)
  const [leadLoading, setLeadLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [errorCanReload, setErrorCanReload] = useState(false)
  const [accounts, setAccounts] = useState<AdminAccountSummary[]>([])
  const [salesOwnerId, setSalesOwnerId] = useState('')
  const [aeOwnerId, setAeOwnerId] = useState('')
  const [contractReference, setContractReference] = useState('')
  const [effectiveDate, setEffectiveDate] = useState(defaultDates.effectiveDate)
  const [slaDueAt, setSlaDueAt] = useState(defaultDates.slaDueAt)
  const [currentAccount, setCurrentAccount] = useState<CurrentAccount | null>(null)
  const [overrideReason, setOverrideReason] = useState('')
  const [workflowHospitalId, setWorkflowHospitalId] = useState<string | null>(null)
  const [workflowHandoff, setWorkflowHandoff] = useState<Handoff | null>(null)
  const [workflowRestoring, setWorkflowRestoring] = useState(true)
  const [creationRequestId, setCreationRequestId] = useState<string | null>(null)
  const [duplicateCandidates, setDuplicateCandidates] = useState<DuplicateCandidate[]>([])
  const [duplicateCheck, setDuplicateCheck] = useState<'idle' | 'checking' | 'clear' | 'duplicate' | 'error'>('idle')
  // 상담 요청 전환이 새 병원을 만드는 대신 기존 병원에 리드를 연결했을 때. 말없이
  // 다른 병원의 온보딩으로 넘어가면 운영자는 자기가 무엇을 만들었는지 알 수 없다.
  const [linkedExistingHospital, setLinkedExistingHospital] = useState(false)

  function openCanonicalOnboarding(hospitalId: string) {
    window.location.replace(`/hospitals/${hospitalId}/onboarding`)
  }

  function rememberWorkflow(hospitalId: string, handoffId: string) {
    window.sessionStorage.setItem(
      ONBOARDING_WORKFLOW_STORAGE_KEY,
      serializeOnboardingWorkflowCheckpoint({ hospitalId, handoffId }),
    )
  }

  function clearRememberedWorkflow() {
    window.sessionStorage.removeItem(ONBOARDING_WORKFLOW_STORAGE_KEY)
    window.sessionStorage.removeItem(ONBOARDING_CREATE_REQUEST_STORAGE_KEY)
  }

  function resetRememberedWorkflow() {
    window.sessionStorage.removeItem(ONBOARDING_WORKFLOW_STORAGE_KEY)
    const requestId = window.crypto.randomUUID()
    window.sessionStorage.setItem(ONBOARDING_CREATE_REQUEST_STORAGE_KEY, requestId)
    setCreationRequestId(requestId)
    setWorkflowHospitalId(null)
    setWorkflowHandoff(null)
  }

  useEffect(() => {
    void fetchCurrentAccount().then(setCurrentAccount)
    fetchAPI<AdminAccountSummary[]>('/admin/accounts').then((rows) => {
      const active = rows.filter((row) => row.is_active)
      setAccounts(active)
      setSalesOwnerId((current) => current || active[0]?.id || '')
      setAeOwnerId((current) => current || active[0]?.id || '')
    }).catch((cause: unknown) => {
      if (!isExpectedOperatorRequestFailure(cause)) throw cause
      setErrorCanReload(true)
      setError(safeOperatorError('onboarding', '운영 화면 다시 불러오기를 눌러 담당자 목록을 다시 확인하세요.'))
    })
  }, [])

  useEffect(() => {
    const hospitalName = name.trim()
    if (!hospitalName || workflowHandoff) {
      setDuplicateCandidates([])
      setDuplicateCheck(workflowHandoff ? 'clear' : 'idle')
      return
    }
    let cancelled = false
    setDuplicateCheck('checking')
    const timer = window.setTimeout(() => {
      fetchAPI<{ candidates: DuplicateCandidate[] }>(`/admin/hospitals/candidates?name=${encodeURIComponent(hospitalName)}`)
        .then((result) => {
          if (cancelled) return
          setDuplicateCandidates(result.candidates)
          setDuplicateCheck(result.candidates.length > 0 ? 'duplicate' : 'clear')
        })
        .catch((cause: unknown) => {
          if (!isExpectedOperatorRequestFailure(cause)) throw cause
          if (!cancelled) {
            setDuplicateCandidates([])
            setDuplicateCheck('error')
          }
        })
    }, 350)
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [name, workflowHandoff])

  useEffect(() => {
    let cancelled = false
    const savedRequestId = parseOnboardingCreateRequestId(
      window.sessionStorage.getItem(ONBOARDING_CREATE_REQUEST_STORAGE_KEY),
    )
    const requestId = savedRequestId ?? window.crypto.randomUUID()
    window.sessionStorage.setItem(ONBOARDING_CREATE_REQUEST_STORAGE_KEY, requestId)
    setCreationRequestId(requestId)
    const checkpoint = parseOnboardingWorkflowCheckpoint(
      window.sessionStorage.getItem(ONBOARDING_WORKFLOW_STORAGE_KEY),
    )
    if (!checkpoint) {
      window.sessionStorage.removeItem(ONBOARDING_WORKFLOW_STORAGE_KEY)
      setWorkflowRestoring(false)
      return
    }

    fetchAPI<Handoff>(`/admin/handoffs/${checkpoint.handoffId}`)
      .then((handoff) => {
        if (cancelled) return
        if (handoff.hospital_id !== checkpoint.hospitalId) {
          resetRememberedWorkflow()
          setError('저장된 온보딩 진행 정보가 일치하지 않아 초기화했습니다. 다시 등록해 주세요.')
          return
        }
        if (handoff.state === 'HANDOFF_ACCEPTED') {
          clearRememberedWorkflow()
          router.replace(`/hospitals/${checkpoint.hospitalId}/onboarding`)
          return
        }
        setWorkflowHospitalId(checkpoint.hospitalId)
        setWorkflowHandoff(handoff)
        if (handoff.hospital_name) setName(handoff.hospital_name)
        setSalesOwnerId(handoff.sales_owner_id ?? '')
        setAeOwnerId(handoff.ae_owner_id ?? '')
        if (handoff.plan) setPlan(handoff.plan)
        if (handoff.contract_reference) setContractReference(handoff.contract_reference)
        const savedEffectiveDate = koreanDateInputValue(handoff.contract_effective_at)
        const savedDueAt = koreanDateTimeLocalInputValue(handoff.sla_due_at)
        if (savedEffectiveDate) setEffectiveDate(savedEffectiveDate)
        if (savedDueAt) setSlaDueAt(savedDueAt)
      })
      .catch((cause: unknown) => {
        if (!isExpectedOperatorRequestFailure(cause)) throw cause
        if (!cancelled) {
          if (cause instanceof ApiError && cause.status === 404) {
            resetRememberedWorkflow()
          }
          setErrorCanReload(true)
          setError(safeOperatorError('onboarding', '운영 화면 다시 불러오기를 눌러 저장된 고객 인수 단계를 다시 확인하세요.'))
        }
      })
      .finally(() => {
        if (!cancelled) setWorkflowRestoring(false)
      })
    return () => {
      cancelled = true
    }
  }, [router])

  useEffect(() => {
    let cancelled = false
    const params = new URLSearchParams(window.location.search)
    const context: LeadContext = {
      id: params.get('leadId'),
    }

    if (!context.id) return
    setLeadContext(context)
    setLeadLoading(true)
    fetchAPI<unknown>(`/admin/leads/${context.id}/hospital-candidates`)
      .then((response) => {
        const clinicName = readClinicNameFromLeadContext(response)
        if (!cancelled && clinicName) setName(clinicName)
      })
      .catch((cause: unknown) => {
        if (!isExpectedOperatorRequestFailure(cause)) throw cause
        if (!cancelled) {
          setErrorCanReload(true)
          setError(safeOperatorError('onboarding', '운영 화면 다시 불러오기를 눌러 상담 요청 정보를 다시 확인하세요.'))
        }
      })
      .finally(() => {
        if (!cancelled) setLeadLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return

    setLoading(true)
    setError(null)
    setErrorCanReload(false)
    if (!currentAccount) {
      setError('로그인 운영자 정보를 확인할 수 없습니다. 다시 로그인해 주세요.')
      setLoading(false)
      return
    }
    const decision = acceptanceDecision({
      actorId: currentAccount.accountId,
      actorRole: currentAccount.role,
      aeOwnerId,
      reason: overrideReason,
    })
    if (decision.kind === 'blocked') {
      setError(decision.message)
      setLoading(false)
      return
    }
    let recoveryHandoff = workflowHandoff
    let recoveryHospitalId = workflowHospitalId
    try {
      let hospitalId = workflowHospitalId
      let handoff = workflowHandoff
      if (!hospitalId || !handoff) {
        if (leadContext?.id) {
          const created = await fetchAPI<{
            hospital?: { id: string } | null
            handoff?: Handoff
            duplicate_resolution?: string | null
          }>(`/admin/leads/${leadContext.id}/convert`, {
            method: 'POST',
            body: JSON.stringify({
              hospital_name: name.trim(),
              plan,
              sales_owner_id: salesOwnerId,
              ae_owner_id: aeOwnerId,
              conversion_note: '상담 요청 수동 등록 화면에서 온보딩 시작',
            }),
          })
          hospitalId = created.hospital?.id ?? null
          handoff = created.handoff ?? null
          setLinkedExistingHospital(created.duplicate_resolution === 'LINKED_EXISTING')
        } else {
          if (!creationRequestId) throw new Error('등록 요청 식별자를 준비하지 못했습니다.')
          const created = await fetchAPI<{ id: string; handoff: Handoff }>('/admin/hospitals', {
            method: 'POST',
            body: JSON.stringify({
              name: name.trim(),
              plan,
              sales_owner_id: salesOwnerId,
              ae_owner_id: aeOwnerId,
              onboarding_request_id: creationRequestId,
            }),
          })
          hospitalId = created.id
          handoff = created.handoff
        }
        if (!hospitalId || !handoff) {
          throw new Error('생성된 병원과 고객 인수 기록을 확인할 수 없습니다.')
        }
        setWorkflowHospitalId(hospitalId)
        setWorkflowHandoff(handoff)
        rememberWorkflow(hospitalId, handoff.id)
        recoveryHandoff = handoff
        recoveryHospitalId = hospitalId
      }
      if (handoff.state === 'HANDOFF_ACCEPTED') {
        clearRememberedWorkflow()
        openCanonicalOnboarding(hospitalId)
        return
      }
      if (handoff.state === 'CONTRACT_PENDING') {
        handoff = await fetchAPI<Handoff>(`/admin/handoffs/${handoff.id}/contract`, {
          method: 'POST',
          body: JSON.stringify(contractPayload({
            salesOwnerId, aeOwnerId, contractReference,
            contractEffectiveAt: `${effectiveDate}T00:00:00+09:00`,
            slaDueAt: `${slaDueAt}:00+09:00`, plan,
          }, handoff.version)),
        })
        setWorkflowHandoff(handoff)
        recoveryHandoff = handoff
      }
      if (handoff.state === 'CONTRACTED') {
        const accepted = await fetchAPI<Handoff>(`/admin/handoffs/${handoff.id}/accept`, {
          method: 'POST', body: JSON.stringify(acceptancePayload(handoff.version, decision.reason)),
        })
        setWorkflowHandoff(accepted)
      }
      clearRememberedWorkflow()
      openCanonicalOnboarding(hospitalId)
    } catch (e: unknown) {
      if ((e instanceof ApiError || isExpectedOperatorRequestFailure(e)) && recoveryHandoff?.id && recoveryHospitalId) {
        try {
          const latest = await fetchAPI<Handoff>(`/admin/handoffs/${recoveryHandoff.id}`)
          setWorkflowHospitalId(recoveryHospitalId)
          setWorkflowHandoff(latest)
          setSalesOwnerId(latest.sales_owner_id ?? '')
          setAeOwnerId(latest.ae_owner_id ?? '')
          if (latest.plan) setPlan(latest.plan)
          rememberWorkflow(recoveryHospitalId, latest.id)
          if (latest.state === 'HANDOFF_ACCEPTED') {
            clearRememberedWorkflow()
            openCanonicalOnboarding(recoveryHospitalId)
            return
          }
        } catch (reloadError: unknown) {
          if (!(reloadError instanceof Error)) throw reloadError
        }
      }
      if (!isExpectedOperatorRequestFailure(e)) throw e
      setError(safeOperatorError('onboarding', '입력 내용을 확인한 뒤 ‘등록하고 고객 인수 승인’을 다시 누르세요.'))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-3xl p-8">
      <div className="mb-6">
        <Link href="/hospitals" className="text-sm text-slate-500 hover:text-slate-700">
          ← 목록으로
        </Link>
        <h1 className="text-2xl font-bold text-slate-900 mt-2">신규 병원 온보딩</h1>
      </div>

      <form onSubmit={handleSubmit} className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 space-y-5">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">
            병원명 <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={workflowHandoff !== null}
            placeholder={leadLoading ? '상담 요청에서 병원명을 불러오는 중...' : '계약서에 적힌 병원 이름'}
            required
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
          {linkedExistingHospital && (
            <p className="mt-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-medium text-blue-900">
              같은 병원이 이미 등록되어 있어 새로 만들지 않고 이 상담 요청을 기존 병원에 연결했습니다. 이어지는 단계는 그 병원의 온보딩입니다.
            </p>
          )}
          {duplicateCheck === 'checking' && (
            <p className="mt-2 text-xs text-slate-500">기존 병원과 중복되는지 확인하는 중입니다.</p>
          )}
          {duplicateCheck === 'error' && (
            <p className="mt-2 text-xs font-medium text-red-700">중복 확인을 완료하지 못했습니다. 잠시 후 다시 입력해 주세요.</p>
          )}
          {duplicateCandidates.length > 0 && (
            <div className="mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
              <p className="font-semibold">같은 이름의 병원이 이미 등록되어 있습니다.</p>
              <p className="mt-1 text-xs">중복 생성하지 말고 아래 기존 병원을 확인해 주세요.</p>
              <ul className="mt-2 space-y-1">
                {duplicateCandidates.map((candidate) => (
                  <li key={candidate.id}>
                    <Link className="font-semibold underline" href={candidate.onboarding_url}>
                      {candidate.name} · {candidate.status}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-medium text-slate-700">영업 담당자
            <select required disabled={workflowHandoff !== null} value={salesOwnerId} onChange={(e) => setSalesOwnerId(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm disabled:bg-slate-100">
              {accounts.map((account) => <option key={account.id} value={account.id}>{account.name} · {account.role}</option>)}
            </select>
          </label>
          <label className="block text-sm font-medium text-slate-700">AE 담당자
            <select required disabled={workflowHandoff !== null} value={aeOwnerId} onChange={(e) => setAeOwnerId(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm disabled:bg-slate-100">
              {accounts.map((account) => <option key={account.id} value={account.id}>{account.name} · {account.role}</option>)}
            </select>
          </label>
        </div>

        {currentAccount?.role === 'OWNER' && currentAccount.accountId !== aeOwnerId && (
          <label className="block text-sm font-medium text-slate-700">
            다른 AE 대신 승인하는 사유 <span className="text-red-500">*</span>
            <textarea
              required
              rows={3}
              maxLength={500}
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
              placeholder="예: 담당 AE 휴가로 OWNER가 고객 인수 승인 대행"
            />
            <span className="mt-1 block text-xs font-normal text-slate-500">승인자와 함께 감사 이력에 기록됩니다.</span>
          </label>
        )}

        {currentAccount?.role === 'OPERATOR' && currentAccount.accountId !== aeOwnerId && (
          <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            담당 AE 본인만 인수 승인할 수 있습니다. AE 담당자를 본인으로 선택해 주세요.
          </p>
        )}

        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block text-sm font-medium text-slate-700">계약 번호
            <input required disabled={workflowHandoff?.state === 'CONTRACTED'} value={contractReference} onChange={(e) => setContractReference(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm disabled:bg-slate-100" placeholder="CTR-20260810" />
            <span className="mt-1 block break-keep text-xs font-normal leading-5 text-slate-500">계약 확정 메일 또는 계약 관리의 계약 ID를 넣습니다. 지정된 값이 있으면 그대로 두세요.</span>
          </label>
          <label className="block text-sm font-medium text-slate-700">계약 효력일
            <input required disabled={workflowHandoff?.state === 'CONTRACTED'} type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm disabled:bg-slate-100" />
            <span className="mt-1 block break-keep text-xs font-normal leading-5 text-slate-500">계약서에 적힌 시작일입니다.</span>
          </label>
          <label className="block text-sm font-medium text-slate-700">인수 처리 기한
            <input required disabled={workflowHandoff?.state === 'CONTRACTED'} type="datetime-local" value={slaDueAt} onChange={(e) => setSlaDueAt(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm disabled:bg-slate-100" />
            <span className="mt-1 block break-keep text-xs font-normal leading-5 text-slate-500">이 시각까지 담당 AE가 계약 정보를 확인하고 고객 인수를 승인해야 합니다.</span>
          </label>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">
            월간 운영량 <span className="text-red-500">*</span>
          </label>
          <p className="mb-1.5 text-xs font-normal text-slate-500">요금제·월 발행량·금액은 선택 라벨에 함께 표시됩니다.</p>
          <select
            value={plan}
            disabled={workflowHandoff?.state === 'CONTRACTED'}
            onChange={(e) => setPlan(parsePlanCode(e.target.value))}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white disabled:bg-slate-100"
          >
            <option value="PLAN_12">{PLAN_CONTRACT_LABELS.PLAN_12}</option>
            <option value="PLAN_16">{PLAN_CONTRACT_LABELS.PLAN_16}</option>
            <option value="PLAN_20">{PLAN_CONTRACT_LABELS.PLAN_20}</option>
          </select>
        </div>

        {error && (
          <OperatorIssuePanel message={error} surface="onboarding" onRetry={errorCanReload ? () => window.location.reload() : undefined} retryLabel="운영 화면 다시 불러오기" />
        )}

        {workflowHandoff && (
          <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
            <p className="font-semibold">고객 인수 절차가 아직 완료되지 않았습니다.</p>
            <p className="mt-1">지금 할 일: {handoffNextAction(workflowHandoff)}</p>
            <p className="mt-1 break-keep text-xs leading-5">등록 버튼을 다시 눌러 저장된 단계에서 이어서 진행하세요.</p>
          </div>
        )}

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
          <p className="font-medium text-slate-800">등록 버튼 실행 내용</p>
          <p className="mt-1">
            병원을 생성하고 고객 인수를 승인한 뒤 온보딩 화면으로 이동합니다.
          </p>
        </div>

        <button
          type="submit"
          disabled={loading || leadLoading || workflowRestoring || !creationRequestId || !name.trim() || !salesOwnerId || !aeOwnerId || !contractReference.trim() || !currentAccount || duplicateCheck !== 'clear'}
          className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {workflowRestoring ? '저장된 진행 상태 확인 중...' : leadLoading ? '리드 정보 확인 중...' : loading ? '인수 승인 중...' : '등록하고 고객 인수 승인'}
        </button>
      </form>
    </div>
  )
}
