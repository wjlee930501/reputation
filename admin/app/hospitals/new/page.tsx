'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ApiError, fetchAPI } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { fetchCurrentAccount, type CurrentAccount } from '@/lib/current-account'
import { readClinicNameFromLeadContext } from '@/lib/lead-onboarding'
import { acceptanceDecision, acceptancePayload, contractPayload, handoffNextAction, parsePlanCode } from '@/lib/handoff'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import type { AdminAccountSummary, Handoff, PlanCode } from '@/types'

interface LeadContext {
  id: string | null
}

export default function NewHospitalPage() {
  const router = useRouter()
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
  const [effectiveDate, setEffectiveDate] = useState('2026-08-10')
  const [slaDueAt, setSlaDueAt] = useState('2026-08-11T18:00')
  const [currentAccount, setCurrentAccount] = useState<CurrentAccount | null>(null)
  const [overrideReason, setOverrideReason] = useState('')
  const [workflowHospitalId, setWorkflowHospitalId] = useState<string | null>(null)
  const [workflowHandoff, setWorkflowHandoff] = useState<Handoff | null>(null)

  useEffect(() => {
    void fetchCurrentAccount().then(setCurrentAccount)
    fetchAPI<AdminAccountSummary[]>('/admin/accounts').then((rows) => {
      const active = rows.filter((row) => row.is_active)
      setAccounts(active)
      setSalesOwnerId(active[0]?.id ?? '')
      setAeOwnerId(active[0]?.id ?? '')
    }).catch((cause: unknown) => {
      if (!isExpectedOperatorRequestFailure(cause)) throw cause
      setErrorCanReload(true)
      setError(safeOperatorError('onboarding', '운영 화면 다시 불러오기를 눌러 담당자 목록을 다시 확인하세요.'))
    })
  }, [])

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
    try {
      let hospitalId = workflowHospitalId
      let handoff = workflowHandoff
      if (!hospitalId || !handoff) {
        if (leadContext?.id) {
          const created = await fetchAPI<{ hospital?: { id: string } | null; handoff?: Handoff }>(`/admin/leads/${leadContext.id}/convert`, {
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
        } else {
          const created = await fetchAPI<{ id: string; handoff: Handoff }>('/admin/hospitals', {
            method: 'POST',
            body: JSON.stringify({ name: name.trim(), plan, sales_owner_id: salesOwnerId, ae_owner_id: aeOwnerId }),
          })
          hospitalId = created.id
          handoff = created.handoff
        }
        if (!hospitalId || !handoff) {
          throw new Error('생성된 병원과 고객 인수 기록을 확인할 수 없습니다.')
        }
        setWorkflowHospitalId(hospitalId)
        setWorkflowHandoff(handoff)
        recoveryHandoff = handoff
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
      const accepted = await fetchAPI<Handoff>(`/admin/handoffs/${handoff.id}/accept`, {
        method: 'POST', body: JSON.stringify(acceptancePayload(handoff.version, decision.reason)),
      })
      setWorkflowHandoff(accepted)
      router.push(`/hospitals/${hospitalId}/onboarding`)
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409 && recoveryHandoff?.id) {
        try {
          setWorkflowHandoff(await fetchAPI<Handoff>(`/admin/handoffs/${recoveryHandoff.id}`))
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
        <h1 className="text-2xl font-bold text-slate-900 mt-2">신규 병원 등록</h1>
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
            placeholder={leadLoading ? '상담 요청에서 병원명을 불러오는 중...' : '예: 장편한외과의원'}
            required
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-medium text-slate-700">영업 담당자
            <select required value={salesOwnerId} onChange={(e) => setSalesOwnerId(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm">
              {accounts.map((account) => <option key={account.id} value={account.id}>{account.name} · {account.role}</option>)}
            </select>
          </label>
          <label className="block text-sm font-medium text-slate-700">AE 담당자
            <select required value={aeOwnerId} onChange={(e) => setAeOwnerId(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm">
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
            <input required value={contractReference} onChange={(e) => setContractReference(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm" placeholder="CTR-20260810" />
          </label>
          <label className="block text-sm font-medium text-slate-700">계약 효력일
            <input required type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm" />
          </label>
          <label className="block text-sm font-medium text-slate-700">인수 처리 기한
            <input required type="datetime-local" value={slaDueAt} onChange={(e) => setSlaDueAt(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm" />
            <span className="mt-1 block break-keep text-xs font-normal leading-5 text-slate-500">이 시각까지 담당 AE가 계약 정보를 확인하고 고객 인수를 승인해야 합니다.</span>
          </label>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">
            월간 운영량 <span className="text-red-500">*</span>
          </label>
          <select
            value={plan}
            onChange={(e) => setPlan(parsePlanCode(e.target.value))}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white"
          >
            <option value="PLAN_12">스타터 · 월 12편</option>
            <option value="PLAN_16">그로워 · 월 16편</option>
            <option value="PLAN_20">리더 · 월 20편</option>
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
          <p className="font-medium text-slate-800">다음: 병원 기본 정보 입력</p>
          <p className="mt-1">
            생성 직후 온보딩 화면으로 이동합니다. 부족한 병원 기본 정보, 근거 자료, 운영 기준 상태를 확인할 수 있습니다.
          </p>
        </div>

        <button
          type="submit"
          disabled={loading || leadLoading || !name.trim() || !salesOwnerId || !aeOwnerId || !contractReference.trim() || !currentAccount}
          className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {leadLoading ? '리드 정보 확인 중...' : loading ? '인수 승인 중...' : '등록하고 고객 인수 승인'}
        </button>
      </form>
    </div>
  )
}
