'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { ApiError, fetchAPI } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { fetchCurrentAccount } from '@/lib/current-account'
import { readClinicNameFromLeadContext } from '@/lib/lead-onboarding'
import {
  existingHospitalId,
  registrationBlockReason,
  registrationPayload,
  suggestContractReference,
  todayInKorea,
} from '@/lib/contract-registration'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { PLAN_CONTRACT_LABELS, type AdminAccountSummary, type PlanCode } from '@/types'

const PLAN_OPTIONS: PlanCode[] = ['PLAN_12', 'PLAN_16', 'PLAN_20']

function parsePlan(value: string): PlanCode {
  return (PLAN_OPTIONS as string[]).includes(value) ? (value as PlanCode) : 'PLAN_12'
}

export default function RegisterContractPage() {
  const router = useRouter()
  const [leadId, setLeadId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [contractReference, setContractReference] = useState(() => suggestContractReference())
  const [effectiveDate, setEffectiveDate] = useState(() => todayInKorea())
  const [plan, setPlan] = useState<PlanCode>('PLAN_12')
  const [aeOwnerId, setAeOwnerId] = useState('')
  const [salesOwnerId, setSalesOwnerId] = useState('')
  const [accounts, setAccounts] = useState<AdminAccountSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [leadLoading, setLeadLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [errorCanReload, setErrorCanReload] = useState(false)
  // 같은 병원이 이미 있으면 다시 만드는 길은 없다. 남는 선택지는 그 병원을 여는 것뿐이다.
  const [existingHospital, setExistingHospital] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    void fetchCurrentAccount().then((account) => {
      if (cancelled || !account) return
      // 등록하는 사람이 곧 인수하는 담당 AE다. 다른 사람으로 바꿀 수는 있다.
      setAeOwnerId((current) => current || account.accountId)
      setSalesOwnerId((current) => current || account.accountId)
    })
    fetchAPI<AdminAccountSummary[]>('/admin/accounts')
      .then((rows) => {
        if (!cancelled) setAccounts(rows.filter((row) => row.is_active))
      })
      .catch((cause: unknown) => {
        if (!isExpectedOperatorRequestFailure(cause)) throw cause
        if (!cancelled) {
          setErrorCanReload(true)
          setError(safeOperatorError('onboarding', '운영 화면 다시 불러오기를 눌러 담당자 목록을 다시 확인하세요.'))
        }
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const requested = new URLSearchParams(window.location.search).get('leadId')
    if (!requested) return
    setLeadId(requested)
    setLeadLoading(true)
    fetchAPI<unknown>(`/admin/leads/${requested}/hospital-candidates`)
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

  const form = { name, leadId, contractReference, effectiveDate, plan, aeOwnerId, salesOwnerId }
  const blockReason = registrationBlockReason(form)

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (blockReason) return
    setLoading(true)
    setError(null)
    setErrorCanReload(false)
    setExistingHospital(null)
    try {
      const created = await fetchAPI<{ id: string }>('/admin/hospitals/register-contract', {
        method: 'POST',
        body: JSON.stringify(registrationPayload(form)),
      })
      router.push(`/hospitals/${created.id}/info`)
    } catch (cause: unknown) {
      const duplicate = cause instanceof ApiError && cause.status === 409
        ? existingHospitalId(cause.detail)
        : null
      if (duplicate) {
        setExistingHospital(duplicate)
        setLoading(false)
        return
      }
      if (!isExpectedOperatorRequestFailure(cause)) throw cause
      setError(safeOperatorError('onboarding', '입력한 계약 정보를 확인해 주세요.'))
      setLoading(false)
    }
  }

  return (
    <div className="max-w-2xl p-8">
      <div className="mb-6">
        <Link href="/hospitals" className="text-sm text-slate-500 hover:text-slate-700">
          ← 목록으로
        </Link>
        <h1 className="mt-2 text-2xl font-bold text-slate-900">계약 등록</h1>
        <p className="mt-1 text-sm text-slate-600">
          병원을 만들고 계약을 기록하고 담당 AE 인수까지 한 번에 끝냅니다.
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-5 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <label className="block text-sm font-medium text-slate-700">
          병원명 <span className="text-red-500">*</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={leadLoading ? '상담 요청에서 병원명을 불러오는 중...' : '계약서에 적힌 병원 이름'}
            required
            className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm"
          />
        </label>

        {existingHospital && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <p className="font-semibold">이미 등록된 병원입니다.</p>
            <Link
              href={`/hospitals/${existingHospital}/info`}
              className="mt-2 inline-flex min-h-11 items-center font-semibold underline"
            >
              기존 병원 열기
            </Link>
          </div>
        )}

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-medium text-slate-700">
            계약 번호 <span className="text-red-500">*</span>
            <input
              required
              value={contractReference}
              onChange={(e) => setContractReference(e.target.value)}
              className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm"
            />
            <span className="mt-1 block break-keep text-xs font-normal leading-5 text-slate-500">
              제안한 번호입니다. 계약서의 실제 번호로 고쳐 주세요.
            </span>
          </label>
          <label className="block text-sm font-medium text-slate-700">
            계약 효력일 <span className="text-red-500">*</span>
            <input
              required
              type="date"
              value={effectiveDate}
              onChange={(e) => setEffectiveDate(e.target.value)}
              className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm"
            />
            <span className="mt-1 block break-keep text-xs font-normal leading-5 text-slate-500">
              계약서에 적힌 시작일입니다.
            </span>
          </label>
        </div>

        <label className="block text-sm font-medium text-slate-700">
          {ADMIN_COPY.plan} <span className="text-red-500">*</span>
          <select
            value={plan}
            onChange={(e) => setPlan(parsePlan(e.target.value))}
            className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm"
          >
            {PLAN_OPTIONS.map((code) => (
              <option key={code} value={code}>{PLAN_CONTRACT_LABELS[code]}</option>
            ))}
          </select>
        </label>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-medium text-slate-700">
            {ADMIN_COPY.aeOwner} <span className="text-red-500">*</span>
            <select
              required
              value={aeOwnerId}
              onChange={(e) => setAeOwnerId(e.target.value)}
              className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm"
            >
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>{account.name}</option>
              ))}
            </select>
          </label>
          <label className="block text-sm font-medium text-slate-700">
            영업 담당
            <select
              value={salesOwnerId}
              onChange={(e) => setSalesOwnerId(e.target.value)}
              className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm"
            >
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>{account.name}</option>
              ))}
            </select>
          </label>
        </div>

        {error && (
          <OperatorIssuePanel
            message={error}
            surface="onboarding"
            onRetry={errorCanReload ? () => window.location.reload() : undefined}
            retryLabel="운영 화면 다시 불러오기"
          />
        )}

        {/* 버튼이 왜 안 눌리는지 말해 주지 않으면 운영자는 별표 없는 필수 필드를
            찾아 화면을 훑게 된다(O-1). 지금 막고 있는 것을 그대로 적는다. */}
        {blockReason && <p className="text-xs font-medium text-amber-800">{blockReason}</p>}
        <button
          type="submit"
          disabled={loading || leadLoading || Boolean(blockReason)}
          className="min-h-11 w-full rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? '등록 중...' : '계약 등록'}
        </button>
      </form>
    </div>
  )
}
