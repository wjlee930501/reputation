'use client'

import { useParams } from 'next/navigation'
import { useEffect, useId, useState } from 'react'
import { fetchAPI, autofillProfile } from '@/lib/api'
import type { AutofillResponse, AutofillFieldMeta } from '@/lib/api'
import { useHospitalHeader } from '../hospital-context'
import { DomainSetupPanel } from '../DomainSetupPanel'
import type { DomainProfile } from '../DomainSetupTypes'

interface Treatment {
  name: string
  description: string
}

interface BusinessHours {
  [day: string]: string
}

interface HospitalProfile {
  id: string
  name: string
  slug: string
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
  domain_management_mode?: DomainProfile['domain_management_mode']
  domain_dns_strategy?: DomainProfile['domain_dns_strategy']
  domain_registrar?: string | null
  domain_dns_provider?: string | null
  domain_purchase_note?: string | null
}

const DAYS = ['월', '화', '수', '목', '금', '토', '일']
const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

function TagInput({
  label,
  values,
  onChange,
  badge,
}: {
  label: string
  values: string[]
  onChange: (v: string[]) => void
  badge?: React.ReactNode
}) {
  const [input, setInput] = useState('')
  const inputId = useId()

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
      <label htmlFor={inputId} className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
        {label}
        {badge}
      </label>
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
              aria-label={`${v} 제거`}
              className="hover:text-blue-900 font-bold"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <input
        id={inputId}
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
        className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
      />
    </div>
  )
}

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
      label: 'AI가 참고할 외부 채널',
      hint: '네이버 플레이스와 구글 지도/병원 정보 URL을 등록합니다. AI 답변과 로컬 검색이 우리 병원을 정확히 인식하기 위한 기본 자료입니다.',
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
    hint: 'AI 언급률 비교 대상 병원을 1개 이상 등록하면 리포트 정확도가 올라갑니다.',
    required: false,
    status: (profile.competitors ?? []).length > 0 ? 'done' : 'recommended',
  })

  const hasDomain = trimmed(profile.aeo_domain).length > 0
  items.push({
    key: 'domain',
    label: '커스텀 도메인',
    hint: profile.site_built
      ? '병원이 구입한 도메인을 입력하고 DNS 검증까지 완료합니다.'
      : '병원 정보 허브 준비가 끝난 뒤 커스텀 도메인 연결 카드에서 연결합니다. (지금은 사전 입력만 가능)',
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
    cls: 'bg-slate-50 text-slate-600 border-slate-200',
  },
}

const SOURCE_LABEL_MAP: Record<string, string> = {
  homepage: '홈페이지',
  blog: '블로그',
  naver: '네이버 플레이스',
  inferred: '추론',
}

function sourceLabel(source: string): string {
  return SOURCE_LABEL_MAP[source] ?? source
}

// Fields that autofill can fill as string scalars (for empty-check)
const SCALAR_AUTOFILL_KEYS = [
  'director_name',
  'director_career',
  'director_philosophy',
  'address',
  'phone',
  'website_url',
  'blog_url',
  'kakao_channel_url',
  'naver_place_id',
] as const

type ScalarAutofillKey = (typeof SCALAR_AUTOFILL_KEYS)[number]

function isBlankScalar(val: unknown): boolean {
  if (val === null || val === undefined) return true
  if (typeof val === 'string') return val.trim() === ''
  return false
}

function isBlankArray(val: unknown): boolean {
  if (!Array.isArray(val)) return true
  return val.length === 0
}

function isBlankObject(val: unknown): boolean {
  if (val === null || val === undefined) return true
  if (typeof val === 'object' && !Array.isArray(val)) {
    return Object.values(val as Record<string, unknown>).every((v) => isBlankScalar(v))
  }
  return false
}

interface AutofillModalProps {
  hospitalName: string
  websiteUrl: string
  blogUrl: string
  loading: boolean
  onClose: () => void
  onSubmit: (name: string, websiteUrl: string, blogUrl: string) => void
}

function AutofillModal({
  hospitalName,
  websiteUrl,
  blogUrl,
  loading,
  onClose,
  onSubmit,
}: AutofillModalProps) {
  const [name, setName] = useState(hospitalName)
  const [website, setWebsite] = useState(websiteUrl)
  const [blog, setBlog] = useState(blogUrl)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    onSubmit(name, website, blog)
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={(e) => { if (e.target === e.currentTarget && !loading) onClose() }}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md mx-4 overflow-hidden">
        <div className="px-6 py-5 border-b border-slate-100">
          <h3 className="text-base font-semibold text-slate-900">자동 채우기</h3>
          <p className="text-xs text-slate-500 mt-1">
            홈페이지·블로그·네이버 플레이스를 스크래핑해 프로파일을 자동으로 채웁니다.
            빈 필드만 채우며, 이미 입력된 내용은 덮어쓰지 않습니다.
            <span className="block mt-1 text-slate-400">수집에 약 20~40초가 소요될 수 있습니다.</span>
          </p>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">병원명</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={loading}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">홈페이지 URL</label>
            <input
              type="url"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
              disabled={loading}
              placeholder="https://example.com"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">블로그 URL</label>
            <input
              type="url"
              value={blog}
              onChange={(e) => setBlog(e.target.value)}
              disabled={loading}
              placeholder="https://blog.naver.com/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-60"
            />
          </div>

          {loading && (
            <div className="flex items-center gap-2.5 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
              <svg className="animate-spin h-4 w-4 text-blue-600 shrink-0" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
              </svg>
              <span className="text-sm text-blue-700">온라인 정보 수집 중…</span>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={loading}
              className="px-4 py-2 text-sm font-medium text-slate-600 border border-slate-300 rounded-lg hover:bg-slate-50 disabled:opacity-50 transition-colors"
            >
              취소
            </button>
            <button
              type="submit"
              disabled={loading}
              className="px-4 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {loading ? '수집 중…' : '가져오기'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

interface AiBadgeProps {
  meta: AutofillFieldMeta
}

function AiBadge({ meta }: AiBadgeProps) {
  const pct = Math.round(meta.confidence * 100)
  const label = sourceLabel(meta.source)
  return (
    <span
      title={`출처: ${label} · 신뢰도 ${pct}%`}
      className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-semibold rounded-full border bg-violet-50 text-violet-700 border-violet-200 cursor-default"
    >
      AI {pct}%
    </span>
  )
}

export default function ProfilePage() {
  const params = useParams<{ id: string }>()
  const hospitalId = params.id
  const { refetch: refetchHeader } = useHospitalHeader()
  const [profile, setProfile] = useState<Partial<HospitalProfile>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)

  // Autofill state
  const [autofillOpen, setAutofillOpen] = useState(false)
  const [autofillLoading, setAutofillLoading] = useState(false)
  const [autofillResult, setAutofillResult] = useState<AutofillResponse | null>(null)
  const [aiFilled, setAiFilled] = useState<Record<string, AutofillFieldMeta>>({})

  useEffect(() => {
    fetchAPI<HospitalProfile>(`/admin/hospitals/${hospitalId}`)
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
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [hospitalId])

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
      await fetchAPI(`/admin/hospitals/${hospitalId}/profile`, {
        method: 'PATCH',
        body: JSON.stringify(profile),
      })
      setSuccess(true)
      void refetchHeader() // 프로파일 완료 플래그 등 헤더 진행 점 갱신
      setTimeout(() => setSuccess(false), 3000)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '저장에 실패했습니다.')
    } finally {
      setSaving(false)
    }
  }

  async function handleAutofill(name: string, websiteUrl: string, blogUrl: string) {
    setAutofillLoading(true)
    setError(null)
    try {
      const body: { name?: string; website_url?: string; blog_url?: string } = {}
      if (name.trim()) body.name = name.trim()
      if (websiteUrl.trim()) body.website_url = websiteUrl.trim()
      if (blogUrl.trim()) body.blog_url = blogUrl.trim()

      const result = await autofillProfile(hospitalId, body)
      setAutofillResult(result)

      const { draft, field_meta } = result
      const newAiFilled: Record<string, AutofillFieldMeta> = { ...aiFilled }

      setProfile((prev) => {
        const next = { ...prev }

        // Scalar string fields
        for (const key of SCALAR_AUTOFILL_KEYS) {
          if (key in draft && isBlankScalar(prev[key as keyof HospitalProfile])) {
            const val = draft[key]
            if (typeof val === 'string' && val.trim() !== '') {
              ;(next as Record<string, unknown>)[key] = val
              if (field_meta[key]) newAiFilled[key] = field_meta[key]
            }
          }
        }

        // business_hours (object)
        if ('business_hours' in draft && isBlankObject(prev.business_hours)) {
          const val = draft.business_hours
          if (val && typeof val === 'object' && !Array.isArray(val)) {
            next.business_hours = val as Record<string, string>
            if (field_meta.business_hours) newAiFilled.business_hours = field_meta.business_hours
          }
        }

        // Array fields
        const arrayKeys = ['region', 'specialties', 'keywords', 'competitors'] as const
        for (const key of arrayKeys) {
          if (key in draft && isBlankArray(prev[key])) {
            const val = draft[key]
            if (Array.isArray(val) && val.length > 0) {
              ;(next as Record<string, unknown>)[key] = val as string[]
              if (field_meta[key]) newAiFilled[key] = field_meta[key]
            }
          }
        }

        // treatments
        if ('treatments' in draft && isBlankArray(prev.treatments)) {
          const val = draft.treatments
          if (Array.isArray(val) && val.length > 0) {
            next.treatments = val as Treatment[]
            if (field_meta.treatments) newAiFilled.treatments = field_meta.treatments
          }
        }

        return next
      })

      setAiFilled(newAiFilled)
      setAutofillOpen(false)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '자동 채우기에 실패했습니다.')
    } finally {
      setAutofillLoading(false)
    }
  }

  if (loading) {
    return <div className="p-8 text-slate-500">불러오는 중...</div>
  }

  const checklist = buildChecklist(profile)
  const totalCount = checklist.length
  const doneCount = checklist.filter((c) => c.status === 'done').length
  const requiredItems = checklist.filter((c) => c.required)
  const requiredMissing = requiredItems.filter((c) => c.status !== 'done')
  const requiredReady = requiredMissing.length === 0
  const nextActionLabel = checklist.find((c) => c.status !== 'done')?.label ?? null
  const progressPercent = totalCount === 0 ? 0 : Math.round((doneCount / totalCount) * 100)

  const violationFields = new Set<string>(
    (autofillResult?.violations ?? []).map((v) => v.field)
  )

  function fieldCls(fieldKey: string, isAiFilled: boolean): string {
    if (violationFields.has(fieldKey)) {
      return 'border-red-400 bg-red-50/40'
    }
    if (isAiFilled) {
      return 'border-violet-300 bg-violet-50/30'
    }
    return 'border-slate-300'
  }

  return (
    <>
      {autofillOpen && (
        <AutofillModal
          hospitalName={profile.name ?? ''}
          websiteUrl={profile.website_url ?? ''}
          blogUrl={profile.blog_url ?? ''}
          loading={autofillLoading}
          onClose={() => { if (!autofillLoading) setAutofillOpen(false) }}
          onSubmit={handleAutofill}
        />
      )}
      <form onSubmit={handleSave} className="p-8 max-w-3xl space-y-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-slate-900">프로파일 온보딩</h2>
          <p className="text-sm text-slate-600 mt-1">
            원장 인터뷰, 병원 기본정보, 외부 채널, 진료 항목, 도메인까지 누락 없이 세팅합니다.
          </p>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          {success && (
            <span className="text-sm text-green-600 font-medium">저장되었습니다 ✓</span>
          )}
          <button
            type="button"
            onClick={() => setAutofillOpen(true)}
            disabled={saving || autofillLoading}
            className="px-4 py-2 text-sm font-medium text-slate-700 border border-slate-300 rounded-lg hover:bg-slate-50 disabled:opacity-50 transition-colors"
          >
            자동 채우기
          </button>
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

      {/* Autofill result: violations warning */}
      {autofillResult && autofillResult.violations.length > 0 && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 space-y-2">
          <p className="text-sm font-semibold text-red-800">의료광고 금지 표현이 감지되었습니다</p>
          <p className="text-xs text-red-700">해당 필드를 직접 수정해 주세요. 자동으로 제거되지 않습니다.</p>
          <ul className="space-y-1">
            {autofillResult.violations.map((v) => (
              <li key={v.field} className="text-xs text-red-700">
                <span className="font-medium">{v.field}</span>: {v.expressions.join(', ')}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Autofill result: source status summary */}
      {autofillResult && (
        <div className="bg-slate-50 border border-slate-200 rounded-lg px-4 py-3">
          {autofillResult.draft && Object.keys(autofillResult.draft).length === 0 ? (
            <p className="text-sm text-slate-600">
              온라인에서 자동으로 채울 정보를 찾지 못했습니다. URL을 확인하거나 직접 입력해 주세요.
            </p>
          ) : (
            <p className="text-sm font-medium text-slate-700 mb-2">자동 채우기 결과</p>
          )}
          <ul className="flex flex-wrap gap-x-4 gap-y-1">
            {autofillResult.sources.map((src) => (
              <li key={src.name} className="text-xs text-slate-600 flex items-center gap-1">
                <span
                  className={src.ok ? 'text-emerald-600 font-bold' : 'text-red-500 font-bold'}
                >
                  {src.ok ? '✓' : '✗'}
                </span>
                <span>{sourceLabel(src.name)}</span>
                {!src.ok && src.reason && (
                  <span className="text-slate-400">({src.reason})</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* 온보딩 체크리스트 */}
      <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-6 py-4 border-b border-slate-100 bg-gradient-to-br from-blue-50 via-indigo-50 to-white">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-slate-900">온보딩 체크리스트</h3>
              <p className="text-xs text-slate-600 mt-1">
                {nextActionLabel
                  ? <>다음 작업: <span className="font-medium text-slate-800">{nextActionLabel}</span></>
                  : '모든 항목이 채워졌습니다. 하단의 온보딩 완료 카드에서 초기 진단 리포트와 병원 정보 허브 준비를 시작하세요.'}
              </p>
            </div>
            <div className="text-right shrink-0">
              <div className="text-sm font-semibold text-slate-900">{doneCount}/{totalCount} 완료</div>
              <div className="text-[11px] text-slate-500 mt-0.5">{progressPercent}%</div>
            </div>
          </div>
          <div className="mt-3 h-1.5 w-full rounded-full bg-slate-200 overflow-hidden">
            <div
              className={`h-full ${requiredReady ? 'bg-emerald-500' : 'bg-blue-500'}`}
              style={{ width: `${progressPercent}%` }}
            />
          </div>
        </div>
        <ul className="divide-y divide-slate-100">
          {checklist.map((item) => {
            const chip = STATUS_CHIP[item.status]
            return (
              <li key={item.key} className="flex items-start justify-between gap-3 px-6 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-800">{item.label}</span>
                    {!item.required && (
                      <span className="text-[10px] text-slate-400 uppercase tracking-wide">선택</span>
                    )}
                  </div>
                  <p className="text-xs text-slate-500 mt-0.5">{item.hint}</p>
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
      <section className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="text-base font-semibold text-slate-800">원장 정보</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            인터뷰·기고문·소개자료에서 확인한 내용을 근거로 입력합니다. 진료 철학은 출처가 분명한 문장으로 정리해 주세요.
          </p>
        </div>
        <div>
          <label htmlFor="profile-director-name" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            원장명
            {aiFilled.director_name && <AiBadge meta={aiFilled.director_name} />}
          </label>
          <input
            type="text"
            id="profile-director-name"
            value={profile.director_name ?? ''}
            onChange={(e) => updateField('director_name', e.target.value)}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${fieldCls('director_name', !!aiFilled.director_name)}`}
          />
        </div>
        <div>
          <label htmlFor="profile-director-career" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            약력
            {aiFilled.director_career && <AiBadge meta={aiFilled.director_career} />}
          </label>
          <textarea
            id="profile-director-career"
            value={profile.director_career ?? ''}
            onChange={(e) => updateField('director_career', e.target.value)}
            rows={3}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none ${fieldCls('director_career', !!aiFilled.director_career)}`}
          />
        </div>
        <div>
          <label htmlFor="profile-director-philosophy" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            진료 철학
            {aiFilled.director_philosophy && <AiBadge meta={aiFilled.director_philosophy} />}
          </label>
          <textarea
            id="profile-director-philosophy"
            value={profile.director_philosophy ?? ''}
            onChange={(e) => updateField('director_philosophy', e.target.value)}
            rows={3}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none ${fieldCls('director_philosophy', !!aiFilled.director_philosophy)}`}
          />
        </div>
      </section>

      {/* 병원 연락처 */}
      <section className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="text-base font-semibold text-slate-800">병원 연락처</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            병원 정보 허브와 AI 답변 노출에 그대로 사용되는 공개 정보입니다. 실제 영업 정보와 일치하는지 확인해 주세요.
          </p>
        </div>
        <div>
          <label htmlFor="profile-address" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            주소
            {aiFilled.address && <AiBadge meta={aiFilled.address} />}
          </label>
          <input
            type="text"
            id="profile-address"
            value={profile.address ?? ''}
            onChange={(e) => updateField('address', e.target.value)}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${fieldCls('address', !!aiFilled.address)}`}
          />
        </div>
        <div>
          <label htmlFor="profile-phone" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            전화번호
            {aiFilled.phone && <AiBadge meta={aiFilled.phone} />}
          </label>
          <input
            type="text"
            id="profile-phone"
            value={profile.phone ?? ''}
            onChange={(e) => updateField('phone', e.target.value)}
            placeholder="02-1234-5678"
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${fieldCls('phone', !!aiFilled.phone)}`}
          />
        </div>
        <div>
          <label className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            진료시간
            {aiFilled.business_hours && <AiBadge meta={aiFilled.business_hours} />}
          </label>
          <div className="space-y-2">
            {DAYS.map((day, i) => (
              <div key={DAY_KEYS[i]} className="flex items-center gap-3">
                <span className="w-6 text-sm text-slate-600 font-medium">{day}</span>
                <input
                  type="text"
                  aria-label={`${day}요일 진료시간`}
                  value={profile.business_hours?.[DAY_KEYS[i]] ?? ''}
                  onChange={(e) => updateHours(DAY_KEYS[i], e.target.value)}
                  placeholder="09:00 ~ 18:00 / 휴진"
                  className="flex-1 px-3 py-1.5 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="profile-website-url" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
              홈페이지 URL
              {aiFilled.website_url && <AiBadge meta={aiFilled.website_url} />}
            </label>
            <input
              type="url"
              id="profile-website-url"
              value={profile.website_url ?? ''}
              onChange={(e) => updateField('website_url', e.target.value)}
              className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${fieldCls('website_url', !!aiFilled.website_url)}`}
            />
          </div>
          <div>
            <label htmlFor="profile-blog-url" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
              블로그 URL
              {aiFilled.blog_url && <AiBadge meta={aiFilled.blog_url} />}
            </label>
            <input
              type="url"
              id="profile-blog-url"
              value={profile.blog_url ?? ''}
              onChange={(e) => updateField('blog_url', e.target.value)}
              className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${fieldCls('blog_url', !!aiFilled.blog_url)}`}
            />
          </div>
        </div>
      </section>

      {/* AI가 참고할 외부 채널 */}
      <section className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="text-base font-semibold text-slate-800">AI가 참고할 외부 채널</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            네이버 플레이스·구글 지도/병원 정보 URL과 좌표는 AI 답변과 로컬 검색에서 우리 병원을 인식시키는 기본 자료입니다.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="profile-google-business-url" className="block text-sm font-medium text-slate-700 mb-1.5">구글 병원 정보 URL</label>
            <input
              type="url"
              id="profile-google-business-url"
              value={profile.google_business_profile_url ?? ''}
              onChange={(e) => updateField('google_business_profile_url', e.target.value)}
              placeholder="https://business.google.com/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label htmlFor="profile-google-maps-url" className="block text-sm font-medium text-slate-700 mb-1.5">구글 지도 URL</label>
            <input
              type="url"
              id="profile-google-maps-url"
              value={profile.google_maps_url ?? ''}
              onChange={(e) => updateField('google_maps_url', e.target.value)}
              placeholder="https://maps.google.com/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label htmlFor="profile-naver-place-url" className="block text-sm font-medium text-slate-700 mb-1.5">네이버 플레이스 URL</label>
            <input
              type="url"
              id="profile-naver-place-url"
              value={profile.naver_place_url ?? ''}
              onChange={(e) => updateField('naver_place_url', e.target.value)}
              placeholder="https://naver.me/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label htmlFor="profile-kakao-channel-url" className="block text-sm font-medium text-slate-700 mb-1.5">카카오 채널 URL</label>
            <input
              type="url"
              id="profile-kakao-channel-url"
              value={profile.kakao_channel_url ?? ''}
              onChange={(e) => updateField('kakao_channel_url', e.target.value)}
              placeholder="https://pf.kakao.com/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label htmlFor="profile-latitude" className="block text-sm font-medium text-slate-700 mb-1.5">위도</label>
            <input
              type="number"
              step="0.000001"
              id="profile-latitude"
              value={profile.latitude ?? ''}
              onChange={(e) => updateField('latitude', e.target.value === '' ? null : Number(e.target.value))}
              placeholder="37.497942"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label htmlFor="profile-longitude" className="block text-sm font-medium text-slate-700 mb-1.5">경도</label>
            <input
              type="number"
              step="0.000001"
              id="profile-longitude"
              value={profile.longitude ?? ''}
              onChange={(e) => updateField('longitude', e.target.value === '' ? null : Number(e.target.value))}
              placeholder="127.027621"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>
      </section>

      {/* 운영 기준 정보 */}
      <section className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div>
          <h3 className="text-base font-semibold text-slate-800">운영 기준 정보</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            지역·전문과목·키워드는 콘텐츠 운영 주제와 AI 언급률 측정 질문을 정리하는 기준으로 사용됩니다. 경쟁 병원은 비교 리포트 정확도를 높입니다.
          </p>
        </div>
        <TagInput
          label="지역"
          values={profile.region ?? []}
          onChange={(v) => updateField('region', v)}
          badge={aiFilled.region ? <AiBadge meta={aiFilled.region} /> : undefined}
        />
        <TagInput
          label="전문과목"
          values={profile.specialties ?? []}
          onChange={(v) => updateField('specialties', v)}
          badge={aiFilled.specialties ? <AiBadge meta={aiFilled.specialties} /> : undefined}
        />
        <TagInput
          label="핵심 키워드"
          values={profile.keywords ?? []}
          onChange={(v) => updateField('keywords', v)}
          badge={aiFilled.keywords ? <AiBadge meta={aiFilled.keywords} /> : undefined}
        />
        <TagInput
          label="경쟁 병원"
          values={profile.competitors ?? []}
          onChange={(v) => updateField('competitors', v)}
          badge={aiFilled.competitors ? <AiBadge meta={aiFilled.competitors} /> : undefined}
        />
      </section>

      {/* 진료 항목 */}
      <section className="bg-white rounded-xl border border-slate-200 p-6 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold text-slate-800">진료 항목</h3>
              {aiFilled.treatments && <AiBadge meta={aiFilled.treatments} />}
            </div>
            <p className="text-xs text-slate-500 mt-0.5">
              시술·치료 안내와 질환 가이드 콘텐츠 자동 생성의 기준이 됩니다. 실제 진료하는 항목만 입력해 주세요.
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
                  className="px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
                <input
                  type="text"
                  value={t.description}
                  onChange={(e) => updateTreatment(i, 'description', e.target.value)}
                  placeholder="설명"
                  className="px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
              <button
                type="button"
                onClick={() => removeTreatment(i)}
                aria-label={`${t.name || '진료 항목'} 제거`}
                className="mt-2 text-slate-400 hover:text-red-500 transition-colors text-lg leading-none"
              >
                ×
              </button>
            </div>
          ))}
          {(profile.treatments ?? []).length === 0 && (
            <p className="text-sm text-slate-400">진료 항목을 추가해 주세요.</p>
          )}
        </div>
      </section>

      {profile.site_built && (
        <DomainSetupPanel
          hospitalId={hospitalId}
          profile={profile}
          onProfileChange={(patch) => setProfile((prev) => ({ ...prev, ...patch }))}
          onHeaderRefresh={() => { void refetchHeader() }}
        />
      )}

      {/* 온보딩 완료 및 초기 리포트/콘텐츠 허브 노출 준비 시작 */}
      <section
        className={`rounded-xl border overflow-hidden ${
          requiredReady
            ? 'border-emerald-200 bg-gradient-to-br from-emerald-50 via-white to-white'
            : 'border-amber-200 bg-gradient-to-br from-amber-50 via-white to-white'
        }`}
      >
        <div className="px-6 py-5 border-b border-slate-100/80">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-slate-900">온보딩 완료 및 초기 진단 리포트·병원 정보 허브 준비 시작</h3>
              <p className="text-xs text-slate-600 mt-1">
                체크 후 저장하면 초기 진단 리포트 생성과 병원 정보 허브 준비가 자동으로 시작됩니다.
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
                필수 항목이 비어 있으면 초기 진단 리포트 정확도가 떨어지고 병원 정보 허브 결과물이 부실해질 수 있습니다.
              </p>
            </div>
          )}

          {requiredReady && (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50/70 px-4 py-3">
              <div className="flex items-center gap-2">
                <span className="inline-block w-2 h-2 rounded-full bg-emerald-500" />
                <p className="text-xs font-semibold text-emerald-800">
                  필수 항목이 모두 준비되었습니다. 초기 진단 리포트와 병원 정보 허브 준비를 시작할 수 있습니다.
                </p>
              </div>
            </div>
          )}

          <label className="flex items-start gap-3 cursor-pointer rounded-lg border border-slate-200 bg-white px-4 py-3 hover:border-slate-300 transition-colors">
            <input
              type="checkbox"
              checked={profile.profile_complete ?? false}
              onChange={(e) => updateField('profile_complete', e.target.checked)}
              className="mt-0.5 w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
            />
            <div>
              <span className="text-sm font-medium text-slate-800">프로파일 완료로 표시</span>
              <p className="text-xs text-slate-500 mt-0.5">
                저장 시점에 초기 진단 리포트 생성과 병원 정보 허브 준비가 자동으로 시작됩니다. 운영 알림으로 결과를 확인합니다.
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
    </>
  )
}
