'use client'

import { useEffect, useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { fetchAPI } from '@/lib/api'

interface LeadContext {
  id: string | null
  type: string | null
  contact: string | null
  question: string | null
  source: string | null
}

export default function NewHospitalPage() {
  const router = useRouter()
  const [name, setName] = useState('')
  const [plan, setPlan] = useState('PLAN_16')
  const [leadContext, setLeadContext] = useState<LeadContext | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const leadName = params.get('name')
    const context: LeadContext = {
      id: params.get('leadId'),
      type: params.get('type'),
      contact: params.get('contact'),
      question: params.get('question'),
      source: params.get('source'),
    }

    if (leadName) setName(leadName)
    if (Object.values(context).some(Boolean)) setLeadContext(context)
  }, [])

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return

    setLoading(true)
    setError(null)
    try {
      const hospital = leadContext?.id
        ? await fetchAPI(`/admin/leads/${leadContext.id}/convert`, {
            method: 'POST',
            body: JSON.stringify({
              hospital_name: name.trim(),
              plan,
              conversion_note: '상담 리드 수동 등록 화면에서 온보딩 시작',
            }),
          }).then((result) => result?.hospital)
        : await fetchAPI('/admin/hospitals', {
            method: 'POST',
            body: JSON.stringify({ name: name.trim(), plan }),
          })
      if (!hospital?.id) {
        throw new Error('생성된 병원 정보를 확인할 수 없습니다.')
      }
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
        <h1 className="mt-2 text-2xl font-bold text-slate-900">신규 병원 온보딩</h1>
        <p className="mt-1 text-sm text-slate-500">
          병원 워크스페이스를 만든 뒤 온보딩 허브에서 프로파일, 자료 인입, 운영 기준을 이어서 진행합니다.
        </p>
      </div>

      {leadContext && (
        <div className="mb-5 rounded-xl border border-blue-100 bg-blue-50 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-blue-950">상담 리드에서 시작됨</p>
              <p className="mt-1 text-xs text-blue-700">
                리드 정보를 병원명 초안으로 불러왔습니다. 생성 후 온보딩 허브에서 세부 정보를 보완하세요.
              </p>
            </div>
            {leadContext.id && (
              <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-medium text-blue-700">
                Lead {leadContext.id.slice(0, 8)}
              </span>
            )}
          </div>
          <dl className="mt-3 grid gap-3 text-xs text-blue-900 sm:grid-cols-2">
            {leadContext.type && (
              <div>
                <dt className="font-medium text-blue-700">진료과/지역</dt>
                <dd className="mt-0.5">{leadContext.type}</dd>
              </div>
            )}
            {leadContext.contact && (
              <div>
                <dt className="font-medium text-blue-700">연락처</dt>
                <dd className="mt-0.5">{leadContext.contact}</dd>
              </div>
            )}
            {leadContext.source && (
              <div>
                <dt className="font-medium text-blue-700">유입</dt>
                <dd className="mt-0.5">{leadContext.source}</dd>
              </div>
            )}
            {leadContext.question && (
              <div className="sm:col-span-2">
                <dt className="font-medium text-blue-700">문의 내용</dt>
                <dd className="mt-0.5">{leadContext.question}</dd>
              </div>
            )}
          </dl>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-5 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <div>
          <label className="mb-1.5 block text-sm font-medium text-slate-700">
            병원명 <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="예: 장편한외과의원"
            required
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-medium text-slate-700">
            월간 운영량 <span className="text-red-500">*</span>
          </label>
          <select
            value={plan}
            onChange={(e) => setPlan(e.target.value)}
            className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-transparent focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value="PLAN_16">월 16편 집중 운영</option>
            <option value="PLAN_12">월 12편 표준 운영</option>
            <option value="PLAN_8">월 8편 기본 운영</option>
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
          disabled={loading || !name.trim()}
          className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {loading ? '등록 중...' : '등록 후 온보딩 허브로 이동'}
        </button>
      </form>
    </div>
  )
}
