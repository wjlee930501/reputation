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
            placeholder="예: 장편한외과의원"
            required
            className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
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
