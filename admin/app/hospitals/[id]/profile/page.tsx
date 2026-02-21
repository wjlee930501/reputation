'use client'

import { useEffect, useState } from 'react'
import { fetchAPI } from '@/lib/api'

interface Treatment {
  name: string
  description: string
}

interface BusinessHours {
  [day: string]: string
}

interface HospitalProfile {
  id: number
  name: string
  plan: string
  status: string
  director_name: string
  director_career: string
  director_philosophy: string
  address: string
  phone: string
  business_hours: BusinessHours
  website_url: string
  blog_url: string
  region: string[]
  specialties: string[]
  keywords: string[]
  competitors: string[]
  treatments: Treatment[]
  profile_complete: boolean
  site_built?: boolean
  site_live?: boolean
  aeo_domain?: string
}

const DAYS = ['월', '화', '수', '목', '금', '토', '일']
const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

function TagInput({
  label,
  values,
  onChange,
}: {
  label: string
  values: string[]
  onChange: (v: string[]) => void
}) {
  const [input, setInput] = useState('')

  function addTag(raw: string) {
    const tags = raw
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    const next = [...values]
    for (const t of tags) {
      if (!next.includes(t)) next.push(t)
    }
    onChange(next)
    setInput('')
  }

  function removeTag(tag: string) {
    onChange(values.filter((v) => v !== tag))
  }

  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1.5">{label}</label>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {values.map((v) => (
          <span
            key={v}
            className="inline-flex items-center gap-1 px-2.5 py-1 bg-blue-50 text-blue-700 text-xs rounded-full"
          >
            {v}
            <button
              type="button"
              onClick={() => removeTag(v)}
              className="hover:text-blue-900 font-bold"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <input
        type="text"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') {
            e.preventDefault()
            if (input.trim()) addTag(input)
          }
        }}
        onBlur={() => { if (input.trim()) addTag(input) }}
        placeholder="입력 후 Enter 또는 쉼표로 추가"
        className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
      />
    </div>
  )
}

export default function ProfilePage({ params }: { params: { id: string } }) {
  const [profile, setProfile] = useState<Partial<HospitalProfile>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  useEffect(() => {
    fetchAPI(`/admin/hospitals/${params.id}`)
      .then((data) => {
        setProfile({
          ...data,
          business_hours: data.business_hours ?? {},
          region: data.region ?? [],
          specialties: data.specialties ?? [],
          keywords: data.keywords ?? [],
          competitors: data.competitors ?? [],
          treatments: data.treatments ?? [],
        })
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [params.id])

  function updateField<K extends keyof HospitalProfile>(key: K, value: HospitalProfile[K]) {
    setProfile((prev) => ({ ...prev, [key]: value }))
  }

  function updateHours(dayKey: string, value: string) {
    setProfile((prev) => ({
      ...prev,
      business_hours: { ...(prev.business_hours ?? {}), [dayKey]: value },
    }))
  }

  function updateTreatment(index: number, field: keyof Treatment, value: string) {
    const next = [...(profile.treatments ?? [])]
    next[index] = { ...next[index], [field]: value }
    updateField('treatments', next)
  }

  function addTreatment() {
    updateField('treatments', [...(profile.treatments ?? []), { name: '', description: '' }])
  }

  function removeTreatment(index: number) {
    updateField(
      'treatments',
      (profile.treatments ?? []).filter((_, i) => i !== index)
    )
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError(null)
    setSuccess(false)
    try {
      await fetchAPI(`/admin/hospitals/${params.id}/profile`, {
        method: 'PATCH',
        body: JSON.stringify(profile),
      })
      setSuccess(true)
      setTimeout(() => setSuccess(false), 3000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '저장에 실패했습니다.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <div className="p-8 text-gray-500">불러오는 중...</div>
  }

  return (
    <form onSubmit={handleSave} className="p-8 max-w-3xl space-y-8">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-bold text-gray-900">프로파일 편집</h2>
        <div className="flex items-center gap-3">
          {success && (
            <span className="text-sm text-green-600 font-medium">저장되었습니다 ✓</span>
          )}
          <button
            type="submit"
            disabled={saving}
            className="px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {saving ? '저장 중...' : '저장'}
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-red-700 text-sm">
          {error}
        </div>
      )}

      {/* 원장 정보 */}
      <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
        <h3 className="text-base font-semibold text-gray-800">원장 정보</h3>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">원장명</label>
          <input
            type="text"
            value={profile.director_name ?? ''}
            onChange={(e) => updateField('director_name', e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">약력</label>
          <textarea
            value={profile.director_career ?? ''}
            onChange={(e) => updateField('director_career', e.target.value)}
            rows={3}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">진료 철학</label>
          <textarea
            value={profile.director_philosophy ?? ''}
            onChange={(e) => updateField('director_philosophy', e.target.value)}
            rows={3}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none"
          />
        </div>
      </section>

      {/* 병원 연락처 */}
      <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
        <h3 className="text-base font-semibold text-gray-800">병원 연락처</h3>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">주소</label>
          <input
            type="text"
            value={profile.address ?? ''}
            onChange={(e) => updateField('address', e.target.value)}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">전화번호</label>
          <input
            type="text"
            value={profile.phone ?? ''}
            onChange={(e) => updateField('phone', e.target.value)}
            placeholder="02-1234-5678"
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1.5">진료시간</label>
          <div className="space-y-2">
            {DAYS.map((day, i) => (
              <div key={DAY_KEYS[i]} className="flex items-center gap-3">
                <span className="w-6 text-sm text-gray-600 font-medium">{day}</span>
                <input
                  type="text"
                  value={profile.business_hours?.[DAY_KEYS[i]] ?? ''}
                  onChange={(e) => updateHours(DAY_KEYS[i], e.target.value)}
                  placeholder="09:00 ~ 18:00 / 휴진"
                  className="flex-1 px-3 py-1.5 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">홈페이지 URL</label>
            <input
              type="url"
              value={profile.website_url ?? ''}
              onChange={(e) => updateField('website_url', e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">블로그 URL</label>
            <input
              type="url"
              value={profile.blog_url ?? ''}
              onChange={(e) => updateField('blog_url', e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>
      </section>

      {/* 타겟 정보 */}
      <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
        <h3 className="text-base font-semibold text-gray-800">타겟 정보</h3>
        <TagInput
          label="지역"
          values={profile.region ?? []}
          onChange={(v) => updateField('region', v)}
        />
        <TagInput
          label="전문과목"
          values={profile.specialties ?? []}
          onChange={(v) => updateField('specialties', v)}
        />
        <TagInput
          label="핵심 키워드"
          values={profile.keywords ?? []}
          onChange={(v) => updateField('keywords', v)}
        />
        <TagInput
          label="경쟁 병원"
          values={profile.competitors ?? []}
          onChange={(v) => updateField('competitors', v)}
        />
      </section>

      {/* 진료 항목 */}
      <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-800">진료 항목</h3>
          <button
            type="button"
            onClick={addTreatment}
            className="px-3 py-1.5 text-xs font-medium text-blue-600 border border-blue-300 rounded-lg hover:bg-blue-50 transition-colors"
          >
            + 추가
          </button>
        </div>
        <div className="space-y-3">
          {(profile.treatments ?? []).map((t, i) => (
            <div key={i} className="flex gap-3 items-start">
              <div className="flex-1 grid grid-cols-2 gap-3">
                <input
                  type="text"
                  value={t.name}
                  onChange={(e) => updateTreatment(i, 'name', e.target.value)}
                  placeholder="항목명 (예: 하지정맥류)"
                  className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
                <input
                  type="text"
                  value={t.description}
                  onChange={(e) => updateTreatment(i, 'description', e.target.value)}
                  placeholder="설명"
                  className="px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
              <button
                type="button"
                onClick={() => removeTreatment(i)}
                className="mt-2 text-gray-400 hover:text-red-500 transition-colors text-lg leading-none"
              >
                ×
              </button>
            </div>
          ))}
          {(profile.treatments ?? []).length === 0 && (
            <p className="text-sm text-gray-400">진료 항목을 추가해 주세요.</p>
          )}
        </div>
      </section>

      {/* 도메인 연결 */}
      {profile.site_built && (
        <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
          <h3 className="text-base font-semibold text-gray-800">도메인 연결</h3>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">AEO 도메인</label>
            <div className="flex gap-2">
              <input
                type="text"
                value={profile.aeo_domain ?? ''}
                onChange={(e) => updateField('aeo_domain', e.target.value)}
                placeholder="예: info.hospital.com"
                className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <button
                type="button"
                onClick={async () => {
                  const domain = profile.aeo_domain
                  if (!domain) return
                  try {
                    await fetchAPI(`/admin/hospitals/${params.id}/domain`, {
                      method: 'PATCH',
                      body: JSON.stringify({ domain }),
                    })
                    setSuccess(true)
                    setTimeout(() => setSuccess(false), 3000)
                  } catch (e: unknown) {
                    setError(e instanceof Error ? e.message : '도메인 연결에 실패했습니다.')
                  }
                }}
                className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700"
              >
                연결
              </button>
            </div>
          </div>
          <div className="bg-gray-50 rounded-lg p-4 text-xs text-gray-600 space-y-1">
            <p className="font-medium text-gray-700 mb-2">DNS 연결 가이드</p>
            <p>1. 도메인 등록업체 DNS 관리 페이지 접속</p>
            <p>2. CNAME 레코드 추가: <code className="bg-gray-200 px-1 rounded">@ → aeo.motionlabs.io</code></p>
            <p>3. TTL: 300 (5분) 설정</p>
            <p>4. 적용까지 최대 24시간 소요</p>
            <p>5. 연결 확인 후 아래 [LIVE 활성화] 버튼 클릭</p>
          </div>
          {profile.aeo_domain && !profile.site_live && (
            <button
              type="button"
              onClick={async () => {
                if (!confirm('도메인 DNS 연결이 완료되었나요? LIVE 상태로 변경합니다.')) return
                try {
                  await fetchAPI(`/admin/hospitals/${params.id}/activate`, {
                    method: 'PATCH',
                  })
                  setProfile((prev) => ({ ...prev, site_live: true }))
                  setSuccess(true)
                  setTimeout(() => setSuccess(false), 3000)
                } catch (e: unknown) {
                  setError(e instanceof Error ? e.message : '활성화에 실패했습니다.')
                }
              }}
              className="w-full py-2.5 bg-green-600 text-white text-sm font-medium rounded-lg hover:bg-green-700 transition-colors"
            >
              LIVE 활성화
            </button>
          )}
          {profile.site_live && (
            <div className="flex items-center gap-2 text-green-700 text-sm font-medium">
              <span>LIVE</span>
              <span className="text-xs text-gray-500">— 사이트가 활성화되었습니다</span>
            </div>
          )}
        </section>
      )}

      {/* 프로파일 완료 */}
      <section className="bg-white rounded-xl border border-gray-200 p-6">
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={profile.profile_complete ?? false}
            onChange={(e) => updateField('profile_complete', e.target.checked)}
            className="w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
          />
          <div>
            <span className="text-sm font-medium text-gray-800">프로파일 완료</span>
            <p className="text-xs text-gray-500 mt-0.5">
              체크 시 V0 리포트 생성 및 AEO 사이트 빌드가 시작됩니다.
            </p>
          </div>
        </label>
      </section>
    </form>
  )
}
