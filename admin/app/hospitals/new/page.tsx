'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import { fetchAPI } from '@/lib/api'

export default function NewHospitalPage() {
  const router = useRouter()
  const [name, setName] = useState('')
  const [plan, setPlan] = useState('PLAN_16')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return

    setLoading(true)
    setError(null)
    try {
      const hospital = await fetchAPI('/admin/hospitals', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), plan }),
      })
      router.push(`/hospitals/${hospital.id}/profile`)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '등록에 실패했습니다.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-8 max-w-lg">
      <div className="mb-6">
        <Link href="/hospitals" className="text-sm text-gray-500 hover:text-gray-700">
          ← 목록으로
        </Link>
        <h1 className="text-2xl font-bold text-gray-900 mt-2">신규 병원 등록</h1>
      </div>

      <form onSubmit={handleSubmit} className="bg-white rounded-xl shadow-sm border border-gray-200 p-6 space-y-5">
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            병원명 <span className="text-red-500">*</span>
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="예: 장편한외과의원"
            required
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">
            요금제 <span className="text-red-500">*</span>
          </label>
          <select
            value={plan}
            onChange={(e) => setPlan(e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent bg-white"
          >
            <option value="PLAN_16">PLAN_16 — 16편/월</option>
            <option value="PLAN_12">PLAN_12 — 12편/월</option>
            <option value="PLAN_8">PLAN_8 — 8편/월</option>
          </select>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-red-700 text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={loading || !name.trim()}
          className="w-full py-2.5 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {loading ? '등록 중...' : '등록 후 프로파일 입력'}
        </button>
      </form>
    </div>
  )
}
