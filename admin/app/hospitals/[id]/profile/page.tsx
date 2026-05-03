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
  kakao_channel_url: string
  google_business_profile_url: string
  google_maps_url: string
  naver_place_url: string
  latitude: number | null
  longitude: number | null
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

const DEFAULT_CNAME_TARGET = 'aeo.motionlabs.io'

type DomainFeedback = { tone: 'success' | 'error' | 'info'; message: string } | null

type ChecklistStatus = 'done' | 'required' | 'recommended'

interface ChecklistItem {
  key: string
  label: string
  hint: string
  status: ChecklistStatus
  required: boolean
}

function trimmed(value: string | null | undefined): string {
  return (value ?? '').trim()
}

function buildChecklist(profile: Partial<HospitalProfile>): ChecklistItem[] {
  const businessHours = profile.business_hours ?? {}
  const hasAnyHour = Object.values(businessHours).some((v) => trimmed(v).length > 0)
  const treatments = profile.treatments ?? []
  const hasNamedTreatment = treatments.some((t) => trimmed(t.name).length > 0)
  const hasCoords =
    typeof profile.latitude === 'number' &&
    typeof profile.longitude === 'number' &&
    !Number.isNaN(profile.latitude) &&
    !Number.isNaN(profile.longitude)
  const hasGoogleAsset =
    trimmed(profile.google_maps_url).length > 0 ||
    trimmed(profile.google_business_profile_url).length > 0

  const required: Array<Omit<ChecklistItem, 'status'>> = [
    {
      key: 'director_basic',
      label: '원장 기본정보',
      hint: '원장명과 약력을 모두 입력합니다.',
      required: true,
    },
    {
      key: 'director_philosophy',
      label: '진료 철학',
      hint: '원장 인터뷰에서 확인한 진료 철학을 한 단락으로 정리합니다.',
      required: true,
    },
    {
      key: 'contact',
      label: '병원 연락처',
      hint: '주소·전화번호·진료시간(요일 1개 이상)을 모두 채웁니다.',
      required: true,
    },
    {
      key: 'web_channels',
      label: '홈페이지/블로그',
      hint: '병원 홈페이지 또는 블로그 URL 중 하나는 등록합니다.',
      required: true,
    },
    {
      key: 'ai_channels',
      label: 'AI 검색 채널',
      hint: '네이버 플레이스와 Google Maps(또는 Google Business Profile) URL을 등록합니다.',
      required: true,
    },
    {
      key: 'geo',
      label: '좌표/지역 정보',
      hint: '위·경도 좌표와 지역 태그를 등록합니다.',
      required: true,
    },
    {
      key: 'targeting',
      label: '전문과목/키워드',
      hint: '전문과목과 핵심 키워드를 각 1개 이상 등록합니다.',
      required: true,
    },
    {
      key: 'treatments',
      label: '진료 항목',
      hint: '콘텐츠 생성의 기준이 되는 진료 항목을 1개 이상 등록합니다.',
      required: true,
    },
  ]

  const requiredStatus: Record<string, boolean> = {
    director_basic: trimmed(profile.director_name).length > 0 && trimmed(profile.director_career).length > 0,
    director_philosophy: trimmed(profile.director_philosophy).length > 0,
    contact: trimmed(profile.address).length > 0 && trimmed(profile.phone).length > 0 && hasAnyHour,
    web_channels: trimmed(profile.website_url).length > 0 || trimmed(profile.blog_url).length > 0,
    ai_channels: trimmed(profile.naver_place_url).length > 0 && hasGoogleAsset,
    geo: hasCoords && (profile.region ?? []).length > 0,
    targeting: (profile.specialties ?? []).length > 0 && (profile.keywords ?? []).length > 0,
    treatments: hasNamedTreatment,
  }

  const items: ChecklistItem[] = required.map((r) => ({
    ...r,
    status: requiredStatus[r.key] ? 'done' : 'required',
  }))

  items.push({
    key: 'competitors',
    label: '경쟁 병원',
    hint: 'SoV 비교 대상 병원을 1개 이상 등록하면 리포트 정확도가 올라갑니다.',
    required: false,
    status: (profile.competitors ?? []).length > 0 ? 'done' : 'recommended',
  })

  const hasDomain = trimmed(profile.aeo_domain).length > 0
  items.push({
    key: 'domain',
    label: '고객 병원 소유 도메인',
    hint: profile.site_built
      ? '발급된 도메인을 입력하고 DNS 검증까지 완료합니다.'
      : '사이트 빌드 후 도메인 카드에서 연결합니다. (지금은 사전 입력만 가능)',
    required: false,
    status: hasDomain ? 'done' : 'recommended',
  })

  return items
}

const STATUS_CHIP: Record<ChecklistStatus, { label: string; cls: string }> = {
  done: {
    label: '완료',
    cls: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  },
  required: {
    label: '필요',
    cls: 'bg-amber-50 text-amber-700 border-amber-200',
  },
  recommended: {
    label: '권장',
    cls: 'bg-gray-50 text-gray-600 border-gray-200',
  },
}

export default function ProfilePage({ params }: { params: { id: string } }) {
  const [profile, setProfile] = useState<Partial<HospitalProfile>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [domainSaving, setDomainSaving] = useState(false)
  const [domainVerifying, setDomainVerifying] = useState(false)
  const [domainFeedback, setDomainFeedback] = useState<DomainFeedback>(null)
  const [domainExpectedCname, setDomainExpectedCname] = useState<string>(DEFAULT_CNAME_TARGET)
  const [domainSavedValue, setDomainSavedValue] = useState<string>('')

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
          latitude: data.latitude ?? null,
          longitude: data.longitude ?? null,
        })
        setDomainSavedValue(data.aeo_domain ?? '')
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

  const checklist = buildChecklist(profile)
  const totalCount = checklist.length
  const doneCount = checklist.filter((c) => c.status === 'done').length
  const requiredItems = checklist.filter((c) => c.required)
  const requiredMissing = requiredItems.filter((c) => c.status !== 'done')
  const requiredReady = requiredMissing.length === 0
  const nextActionLabel = checklist.find((c) => c.status !== 'done')?.label ?? null
  const progressPercent = totalCount === 0 ? 0 : Math.round((doneCount / totalCount) * 100)

  return (
    <form onSubmit={handleSave} className="p-8 max-w-3xl space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-gray-900">프로파일 온보딩</h2>
          <p className="text-sm text-gray-600 mt-1">
            원장 인터뷰, 병원 기본정보, 외부 채널, 진료 항목, 도메인까지 누락 없이 세팅합니다.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
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

      {/* 온보딩 체크리스트 */}
      <section className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100 bg-gradient-to-br from-blue-50 via-indigo-50 to-white">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-gray-900">온보딩 체크리스트</h3>
              <p className="text-xs text-gray-600 mt-1">
                {nextActionLabel
                  ? <>다음 작업: <span className="font-medium text-gray-800">{nextActionLabel}</span></>
                  : '모든 항목이 채워졌습니다. 하단의 온보딩 완료 카드에서 V0 리포트와 사이트 빌드를 시작하세요.'}
              </p>
            </div>
            <div className="text-right shrink-0">
              <div className="text-sm font-semibold text-gray-900">{doneCount}/{totalCount} 완료</div>
              <div className="text-[11px] text-gray-500 mt-0.5">{progressPercent}%</div>
            </div>
          </div>
          <div className="mt-3 h-1.5 w-full rounded-full bg-gray-200 overflow-hidden">
            <div
              className={`h-full ${requiredReady ? 'bg-emerald-500' : 'bg-blue-500'}`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
        <ul className="divide-y divide-gray-100">
          {checklist.map((item) => {
            const chip = STATUS_CHIP[item.status]
            return (
              <li key={item.key} className="flex items-start justify-between gap-3 px-6 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-800">{item.label}</span>
                    {!item.required && (
                      <span className="text-[10px] text-gray-400 uppercase tracking-wide">선택</span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5">{item.hint}</p>
                </div>
                <span
                  className={`shrink-0 inline-flex items-center px-2 py-0.5 text-[11px] font-medium rounded-full border ${chip.cls}`}
                >
                  {chip.label}
                </span>
              </li>
            )
          })}
        </ul>
      </section>

      {/* 원장 정보 */}
      <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
        <div>
          <h3 className="text-base font-semibold text-gray-800">원장 정보</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            인터뷰·기고문·소개자료에서 확인한 내용을 근거로 입력합니다. 진료 철학은 출처가 분명한 문장으로 정리해 주세요.
          </p>
        </div>
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
        <div>
          <h3 className="text-base font-semibold text-gray-800">병원 연락처</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            AEO 사이트와 AI 답변 노출에 그대로 사용되는 공개 정보입니다. 실제 영업 정보와 일치하는지 확인해 주세요.
          </p>
        </div>
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

      {/* AI 검색 자산 */}
      <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
        <div>
          <h3 className="text-base font-semibold text-gray-800">AI 검색 자산</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            네이버 플레이스·Google Maps·Google Business Profile URL과 좌표는 AI 답변과 로컬 검색 노출에 직접 영향을 줍니다.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Google Business Profile URL</label>
            <input
              type="url"
              value={profile.google_business_profile_url ?? ''}
              onChange={(e) => updateField('google_business_profile_url', e.target.value)}
              placeholder="https://business.google.com/..."
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Google Maps URL</label>
            <input
              type="url"
              value={profile.google_maps_url ?? ''}
              onChange={(e) => updateField('google_maps_url', e.target.value)}
              placeholder="https://maps.google.com/..."
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">Naver Place URL</label>
            <input
              type="url"
              value={profile.naver_place_url ?? ''}
              onChange={(e) => updateField('naver_place_url', e.target.value)}
              placeholder="https://naver.me/..."
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">카카오 채널 URL</label>
            <input
              type="url"
              value={profile.kakao_channel_url ?? ''}
              onChange={(e) => updateField('kakao_channel_url', e.target.value)}
              placeholder="https://pf.kakao.com/..."
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">위도</label>
            <input
              type="number"
              step="0.000001"
              value={profile.latitude ?? ''}
              onChange={(e) => updateField('latitude', e.target.value === '' ? null : Number(e.target.value))}
              placeholder="37.497942"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1.5">경도</label>
            <input
              type="number"
              step="0.000001"
              value={profile.longitude ?? ''}
              onChange={(e) => updateField('longitude', e.target.value === '' ? null : Number(e.target.value))}
              placeholder="127.027621"
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>
      </section>

      {/* 타겟 정보 */}
      <section className="bg-white rounded-xl border border-gray-200 p-6 space-y-4">
        <div>
          <h3 className="text-base font-semibold text-gray-800">타겟 정보</h3>
          <p className="text-xs text-gray-500 mt-0.5">
            지역·전문과목·키워드는 LOCAL/AEO 콘텐츠 타게팅과 SoV 측정 쿼리에 사용됩니다. 경쟁 병원은 비교 리포트 정확도를 높입니다.
          </p>
        </div>
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
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-gray-800">진료 항목</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              TREATMENT·DISEASE 콘텐츠 자동 생성의 기준이 됩니다. 실제 진료하는 항목만 입력해 주세요.
            </p>
          </div>
          <button
            type="button"
            onClick={addTreatment}
            className="shrink-0 px-3 py-1.5 text-xs font-medium text-blue-600 border border-blue-300 rounded-lg hover:bg-blue-50 transition-colors"
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

      {/* 도메인 연결 — 원장 소유 도메인 자산 */}
      {profile.site_built && (() => {
        const currentDomain = (profile.aeo_domain ?? '').trim()
        const hasUnsavedChange = currentDomain !== (domainSavedValue ?? '').trim()
        const status: 'live' | 'waiting' | 'unsaved' | 'empty' = hasUnsavedChange
          ? 'unsaved'
          : profile.site_live
            ? 'live'
            : !domainSavedValue
              ? 'empty'
              : 'waiting'

        const statusBadge = {
          live:    { label: '사이트 라이브',    cls: 'bg-emerald-50 text-emerald-700 border-emerald-200' },
          waiting: { label: '검증 대기',        cls: 'bg-amber-50 text-amber-700 border-amber-200' },
          unsaved: { label: '저장 필요',        cls: 'bg-blue-50 text-blue-700 border-blue-200' },
          empty:   { label: '도메인 미설정',    cls: 'bg-gray-50 text-gray-600 border-gray-200' },
        }[status]

        async function handleSaveDomain() {
          const domain = (profile.aeo_domain ?? '').trim()
          if (!domain) {
            setDomainFeedback({ tone: 'error', message: '도메인을 입력해 주세요.' })
            return
          }
          setDomainSaving(true)
          setDomainFeedback(null)
          try {
            await fetchAPI(`/admin/hospitals/${params.id}/domain`, {
              method: 'PATCH',
              body: JSON.stringify({ domain }),
            })
            setDomainSavedValue(domain)
            setProfile((prev) => ({ ...prev, aeo_domain: domain, site_live: false }))
            setDomainFeedback({
              tone: 'success',
              message: '도메인이 저장되었습니다. DNS 전파 후 [DNS 확인하고 사이트 라이브 전환]을 눌러주세요.',
            })
          } catch (e: unknown) {
            setDomainFeedback({
              tone: 'error',
              message: e instanceof Error ? e.message : '도메인 저장에 실패했습니다.',
            })
          } finally {
            setDomainSaving(false)
          }
        }

        async function handleVerifyDomain() {
          setDomainVerifying(true)
          setDomainFeedback(null)
          try {
            const result = await fetchAPI(`/admin/hospitals/${params.id}/domain/verify`, {
              method: 'POST',
            })
            if (result?.expected_cname) setDomainExpectedCname(result.expected_cname)
            if (result?.verified) {
              setProfile((prev) => ({ ...prev, site_live: true }))
              setDomainFeedback({
                tone: 'success',
                message: result.message ?? '도메인 연결이 확인되어 사이트가 라이브 상태로 전환되었습니다.',
              })
            } else {
              setDomainFeedback({
                tone: 'error',
                message: result?.message ?? 'CNAME 설정이 아직 확인되지 않았습니다. DNS 전파에는 최대 24시간이 걸릴 수 있습니다.',
              })
            }
          } catch (e: unknown) {
            setDomainFeedback({
              tone: 'error',
              message: e instanceof Error ? e.message : '도메인 검증에 실패했습니다.',
            })
          } finally {
            setDomainVerifying(false)
          }
        }

        return (
          <section className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            {/* Hero */}
            <div className="bg-gradient-to-br from-indigo-50 via-blue-50 to-white px-6 py-5 border-b border-gray-100">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h3 className="text-base font-semibold text-gray-900">원장 소유 도메인 연결</h3>
                  <p className="text-sm text-gray-700 mt-1">
                    원장님 소유 도메인으로 AI 검색 자산을 운영합니다.
                  </p>
                  <p className="text-xs text-gray-500 mt-1">
                    계정·콘텐츠가 플랫폼에 종속되지 않고 병원 브랜드 자산으로 남습니다.
                  </p>
                </div>
                <span
                  className={`shrink-0 inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-full border ${statusBadge.cls}`}
                >
                  {statusBadge.label}
                </span>
              </div>
            </div>

            <div className="px-6 py-5 space-y-5">
              {/* STEP 1 — 도메인 입력 */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-[11px] font-semibold">1</span>
                  <label className="text-sm font-semibold text-gray-800">원장님 소유 도메인</label>
                </div>
                <p className="text-xs text-gray-500 mb-2">
                  서브도메인 사용을 권장합니다 (예: <code className="px-1 bg-gray-100 rounded">www.clinicname.co.kr</code> 또는 <code className="px-1 bg-gray-100 rounded">ai.clinicname.co.kr</code>).
                  병원이 이미 보유한 도메인을 입력하거나, 신규 도메인을 등록업체에서 발급받아 주세요.
                </p>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={profile.aeo_domain ?? ''}
                    onChange={(e) => updateField('aeo_domain', e.target.value)}
                    placeholder="ai.clinicname.co.kr"
                    className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                  />
                  <button
                    type="button"
                    onClick={handleSaveDomain}
                    disabled={domainSaving || !currentDomain || !hasUnsavedChange}
                    className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {domainSaving ? '저장 중...' : '도메인 저장'}
                  </button>
                </div>
              </div>

              {/* STEP 2 — DNS 안내 */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-[11px] font-semibold">2</span>
                  <label className="text-sm font-semibold text-gray-800">DNS 설정 안내</label>
                </div>
                <p className="text-xs text-gray-500 mb-2">
                  도메인 등록업체(가비아·카페24·후이즈 등)의 DNS 관리 페이지에서 아래 CNAME 레코드를 추가해 주세요.
                </p>
                <div className="rounded-lg border border-gray-200 bg-gray-50 overflow-hidden">
                  <table className="w-full text-xs">
                    <tbody className="divide-y divide-gray-200">
                      <tr>
                        <td className="px-3 py-2 w-32 text-gray-500 font-medium">Type</td>
                        <td className="px-3 py-2 font-mono text-gray-800">CNAME</td>
                      </tr>
                      <tr>
                        <td className="px-3 py-2 text-gray-500 font-medium">Name / Host</td>
                        <td className="px-3 py-2 font-mono text-gray-800">
                          {currentDomain || <span className="text-gray-400">입력한 도메인</span>}
                        </td>
                      </tr>
                      <tr>
                        <td className="px-3 py-2 text-gray-500 font-medium">Value / Target</td>
                        <td className="px-3 py-2 font-mono text-gray-800">{domainExpectedCname}</td>
                      </tr>
                      <tr>
                        <td className="px-3 py-2 text-gray-500 font-medium">TTL</td>
                        <td className="px-3 py-2 font-mono text-gray-800">300 (5분)</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <p className="text-[11px] text-gray-400 mt-1.5">
                  환경별 대상값은 검증 결과 메시지를 기준으로 확인됩니다. DNS 전파에는 최대 24시간이 소요될 수 있습니다.
                </p>
              </div>

              {/* STEP 3 — 검증 및 라이브 전환 */}
              <div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-[11px] font-semibold">3</span>
                  <label className="text-sm font-semibold text-gray-800">연결 검증 및 사이트 라이브</label>
                </div>
                {profile.site_live && !hasUnsavedChange ? (
                  <div className="flex items-center justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="inline-block w-2 h-2 rounded-full bg-emerald-500" />
                        <span className="text-sm font-semibold text-emerald-800">사이트 라이브</span>
                      </div>
                      <p className="text-xs text-emerald-700 mt-0.5">
                        원장님 소유 도메인으로 AEO 사이트가 정상 운영되고 있습니다.
                      </p>
                    </div>
                    {currentDomain && (
                      <a
                        href={`https://${currentDomain}`}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs font-mono text-emerald-700 underline hover:text-emerald-900"
                      >
                        {currentDomain} ↗
                      </a>
                    )}
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={handleVerifyDomain}
                    disabled={domainVerifying || !domainSavedValue || hasUnsavedChange}
                    className="w-full py-2.5 bg-emerald-600 text-white text-sm font-medium rounded-lg hover:bg-emerald-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {domainVerifying
                      ? 'DNS 확인 중...'
                      : hasUnsavedChange
                        ? '변경한 도메인을 먼저 저장해 주세요'
                        : !domainSavedValue
                          ? '도메인을 먼저 저장해 주세요'
                          : 'DNS 확인하고 사이트 라이브 전환'}
                  </button>
                )}
              </div>

              {/* 인라인 피드백 */}
              {domainFeedback && (
                <div
                  className={`rounded-lg px-3 py-2 text-sm border ${
                    domainFeedback.tone === 'success'
                      ? 'bg-emerald-50 border-emerald-200 text-emerald-800'
                      : domainFeedback.tone === 'error'
                        ? 'bg-red-50 border-red-200 text-red-700'
                        : 'bg-blue-50 border-blue-200 text-blue-700'
                  }`}
                >
                  {domainFeedback.message}
                </div>
              )}
            </div>
          </section>
        )
      })()}

      {/* 온보딩 완료 및 V0 리포트/사이트 빌드 시작 */}
      <section
        className={`rounded-xl border overflow-hidden ${
          requiredReady
            ? 'border-emerald-200 bg-gradient-to-br from-emerald-50 via-white to-white'
            : 'border-amber-200 bg-gradient-to-br from-amber-50 via-white to-white'
        }`}
      >
        <div className="px-6 py-5 border-b border-gray-100/80">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-gray-900">온보딩 완료 및 V0 리포트/사이트 빌드 시작</h3>
              <p className="text-xs text-gray-600 mt-1">
                체크 후 저장하면 V0 리포트 생성과 AEO 사이트 빌드가 자동으로 시작됩니다.
                필수 항목을 모두 채운 뒤 진행하세요.
              </p>
            </div>
            <span
              className={`shrink-0 inline-flex items-center px-2.5 py-1 text-xs font-medium rounded-full border ${
                requiredReady
                  ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                  : 'bg-amber-50 text-amber-700 border-amber-200'
              }`}
            >
              {requiredReady
                ? '필수 항목 준비 완료'
                : `필수 ${requiredItems.length - requiredMissing.length}/${requiredItems.length}`}
            </span>
          </div>
        </div>

        <div className="px-6 py-5 space-y-4">
          {!requiredReady && (
            <div className="rounded-lg border border-amber-200 bg-amber-50/70 px-4 py-3">
              <p className="text-xs font-semibold text-amber-800">아직 채워지지 않은 필수 항목</p>
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {requiredMissing.map((m) => (
                  <li
                    key={m.key}
                    className="inline-flex items-center px-2 py-0.5 text-[11px] font-medium rounded-full bg-white border border-amber-300 text-amber-800"
                  >
                    {m.label}
                  </li>
                ))}
              </ul>
              <p className="text-[11px] text-amber-700 mt-2">
                필수 항목이 비어 있으면 V0 리포트 정확도가 떨어지고 AEO 사이트 빌드 결과물이 부실해질 수 있습니다.
              </p>
            </div>
          )}

          {requiredReady && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50/70 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-emerald-500" />
                <p className="text-xs font-semibold text-emerald-800">
                  필수 항목이 모두 준비되었습니다. V0 리포트와 사이트 빌드를 시작할 수 있습니다.
                </p>
              </div>
            </div>
          )}

          <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-gray-200 bg-white px-4 py-3 hover:border-gray-300 transition-colors">
            <input
              type="checkbox"
              checked={profile.profile_complete ?? false}
              onChange={(e) => updateField('profile_complete', e.target.checked)}
              className="mt-0.5 w-4 h-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
            />
            <div>
              <span className="text-sm font-medium text-gray-800">프로파일 완료로 표시</span>
              <p className="text-xs text-gray-500 mt-0.5">
                저장 시점에 V0 리포트와 AEO 사이트 빌드가 트리거됩니다. 빌드 결과는 Slack으로 알림됩니다.
              </p>
              {!requiredReady && (profile.profile_complete ?? false) && (
                <p className="text-[11px] text-amber-700 mt-1.5">
                  필수 항목이 비어 있는 상태로 진행하려고 합니다. 가능하면 위 항목을 먼저 채워 주세요.
                </p>
              )}
            </div>
          </label>
        </div>
      </section>
    </form>
  )
}
