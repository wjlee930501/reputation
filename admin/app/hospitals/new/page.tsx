'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { fetchAPI } from '@/lib/api'
import { readClinicNameFromLeadContext } from '@/lib/lead-onboarding'
import { acceptancePayload, contractPayload } from '@/lib/handoff'
import type { AdminAccountSummary, Handoff, PlanCode } from '@/types'

interface LeadContext {
  id: string | null
}

export default function NewHospitalPage() {
  const router = useRouter()
  const [name, setName] = useState('')
  const [plan, setPlan] = useState('PLAN_12')
  const [leadContext, setLeadContext] = useState<LeadContext | null>(null)
  const [leadLoading, setLeadLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [accounts, setAccounts] = useState<AdminAccountSummary[]>([])
  const [salesOwnerId, setSalesOwnerId] = useState('')
  const [aeOwnerId, setAeOwnerId] = useState('')
  const [contractReference, setContractReference] = useState('')
  const [effectiveDate, setEffectiveDate] = useState('2026-08-10')
  const [slaDueAt, setSlaDueAt] = useState('2026-08-11T18:00')

  useEffect(() => {
    fetchAPI<AdminAccountSummary[]>('/admin/accounts').then((rows) => {
      const active = rows.filter((row) => row.is_active)
      setAccounts(active)
      setSalesOwnerId(active[0]?.id ?? '')
      setAeOwnerId(active[0]?.id ?? '')
    }).catch((cause: unknown) => setError(cause instanceof Error ? cause.message : '담당자 목록을 불러오지 못했습니다.'))
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
        if (!cancelled) setError(cause instanceof Error ? cause.message : '상담 리드 정보를 불러오지 못했습니다.')
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
    try {
      let hospital: { id: string } | null | undefined
      let handoff: Handoff | undefined
      if (leadContext?.id) {
        const created = await fetchAPI<{ hospital?: { id: string } | null; handoff?: Handoff }>(`/admin/leads/${leadContext.id}/convert`, {
            method: 'POST',
            body: JSON.stringify({
              hospital_name: name.trim(),
              plan,
              sales_owner_id: salesOwnerId,
              ae_owner_id: aeOwnerId,
              conversion_note: '상담 리드 수동 등록 화면에서 온보딩 시작',
            }),
          })
        hospital = created.hospital
        handoff = created.handoff
      } else {
        const created = await fetchAPI<{ id: string; handoff: Handoff }>('/admin/hospitals', {
            method: 'POST',
            body: JSON.stringify({ name: name.trim(), plan, sales_owner_id: salesOwnerId, ae_owner_id: aeOwnerId }),
          })
        hospital = created
        handoff = created.handoff
      }
      if (!hospital?.id) {
        throw new Error('생성된 병원 정보를 확인할 수 없습니다.')
      }
      if (!handoff?.id) throw new Error('고객 인수 기록을 확인할 수 없습니다.')
      const contracted = await fetchAPI<Handoff>(`/admin/handoffs/${handoff.id}/contract`, {
        method: 'POST',
        body: JSON.stringify(contractPayload({
          salesOwnerId, aeOwnerId, contractReference,
          contractEffectiveAt: `${effectiveDate}T00:00:00+09:00`,
          slaDueAt: `${slaDueAt}:00+09:00`, plan: plan as PlanCode,
        }, handoff.version)),
      })
      await fetchAPI(`/admin/handoffs/${handoff.id}/accept`, {
        method: 'POST', body: JSON.stringify(acceptancePayload(contracted.version)),
      })
      router.push(`/hospitals/${hospital.id}/onboarding`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '등록에 실패했습니다.')
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
            placeholder={leadLoading ? '상담 리드에서 병원명을 불러오는 중...' : '예: 장편한외과의원'}
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

        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block text-sm font-medium text-slate-700">계약 번호
            <input required value={contractReference} onChange={(e) => setContractReference(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm" placeholder="CTR-20260810" />
          </label>
          <label className="block text-sm font-medium text-slate-700">계약 효력일
            <input required type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm" />
          </label>
          <label className="block text-sm font-medium text-slate-700">인수 SLA
            <input required type="datetime-local" value={slaDueAt} onChange={(e) => setSlaDueAt(e.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-slate-300 px-3 text-sm" />
          </label>
        </div>

        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1.5">
            월간 운영량 <span className="text-red-500">*</span>
          </label>
          <select
            value={plan}
            onChange={(e) => setPlan(e.target.value)}
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white"
          >
            <option value="PLAN_12">스타터 · 월 12편</option>
            <option value="PLAN_16">그로워 · 월 16편</option>
            <option value="PLAN_20">리더 · 월 20편</option>
          </select>
        </div>

        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
          <p className="font-medium text-slate-800">다음: 프로파일 입력</p>
          <p className="mt-1">
            생성 직후 온보딩 허브로 이동합니다. 허브에서 부족한 프로파일, 자료 인입, 운영 기준 상태를 확인할 수 있습니다.
          </p>
        </div>

        <button
          type="submit"
          disabled={loading || leadLoading || !name.trim() || !salesOwnerId || !aeOwnerId || !contractReference.trim()}
          className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {leadLoading ? '리드 정보 확인 중...' : loading ? '인수 승인 중...' : '등록하고 고객 인수 승인'}
        </button>
      </form>
    </div>
  )
}
