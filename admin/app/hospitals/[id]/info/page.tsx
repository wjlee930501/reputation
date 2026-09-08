'use client'

import { useParams } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { ApiError, fetchAPI, autofillProfile } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import { profilePatchPayload } from '@/lib/profile-patch'
import { profileSaveErrorMessage } from '@/lib/profile-save-error'
import {
  INFO_SECTION_TITLES,
  infoSectionAnchorId,
  remainingRequirementsSummary,
  sectionForRequirement,
} from '@/lib/info-sections'
import type { AutofillResponse, AutofillFieldMeta } from '@/lib/api'
import type { MissingProfileRequirement } from '@/lib/info-sections'
import type { ProfileSourceRegistration } from '@/types'
import type { DomainProfile } from '../DomainSetupTypes'
import { useHospitalHeader } from '../hospital-context'
import { DomainSetupPanel } from '../DomainSetupPanel'
import { AutofillModal } from './AutofillModal'
import { BrandSection } from './BrandSection'
import { FactsSection, sourceLabel } from './FactsSection'
import { PhotosSection } from './PhotosSection'
import type { HospitalInfoProfile, Treatment } from './FactsSection'

/** PATCH /profile 응답. 완료 여부·남은 항목은 서버가 판정해 함께 내려준다. */
interface ProfileSaveResponse extends Partial<HospitalInfoProfile> {
  missing_profile_requirements?: MissingProfileRequirement[]
  source_registration?: ProfileSourceRegistration[]
}

/**
 * 폼 상태. 사실 칸에 더해 자기 도메인 칸도 같은 상태에 들고 있어야
 * `DomainSetupPanel`이 입력한 값을 화면이 그대로 다시 보여 줄 수 있다.
 */
type InfoFormProfile = Partial<HospitalInfoProfile> & DomainProfile

// 자동 입력이 문자열 하나로 채울 수 있는 칸(빈 칸인지 판단하는 데 쓴다).
const SCALAR_AUTOFILL_KEYS = [
  'director_name',
  'director_career',
  'director_philosophy',
  'address',
  'phone',
  'website_url',
  'blog_url',
  'kakao_channel_url',
  'naver_place_url',
] as const

function isBlankScalar(val: unknown): boolean {
  if (val === null || val === undefined) return true
  if (typeof val === 'string') return val.trim() === ''
  return false
}

function isBlankArray(val: unknown): boolean {
  return !Array.isArray(val) || val.length === 0
}

function isBlankObject(val: unknown): boolean {
  if (val === null || val === undefined) return true
  if (typeof val === 'object' && !Array.isArray(val)) {
    return Object.values(val as Record<string, unknown>).every((v) => isBlankScalar(v))
  }
  return false
}

export default function HospitalInfoPage() {
  const params = useParams<{ id: string }>()
  const hospitalId = params.id
  const { hospital, loading: headerLoading, refetch: refetchHeader } = useHospitalHeader()
  const [profile, setProfile] = useState<InfoFormProfile>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [savedAddress, setSavedAddress] = useState('')
  const [coordinatesManuallyEdited, setCoordinatesManuallyEdited] = useState(false)
  const [coordinateNotice, setCoordinateNotice] = useState<string | null>(null)
  const [sourceRegistration, setSourceRegistration] = useState<ProfileSourceRegistration[]>([])

  const [autofillOpen, setAutofillOpen] = useState(false)
  const [autofillLoading, setAutofillLoading] = useState(false)
  const [autofillResult, setAutofillResult] = useState<AutofillResponse | null>(null)
  const [aiFilled, setAiFilled] = useState<Record<string, AutofillFieldMeta>>({})

  // 레이아웃이 이미 같은 GET /admin/hospitals/{id}를 받아 컨텍스트에 들고 있다 — 폼을
  // 그걸로 한 번만 채운다. ref로 "한 번만"을 지켜야 저장 직후 refetch가 편집 중인
  // 내용을 서버 스냅샷으로 덮어쓰지 않는다.
  const seededFromHeaderRef = useRef(false)
  useEffect(() => {
    if (seededFromHeaderRef.current || !hospital) return
    seededFromHeaderRef.current = true
    const data = hospital as unknown as HospitalInfoProfile
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
    setSavedAddress(data.address ?? '')
  }, [hospital])

  function updateField<K extends keyof HospitalInfoProfile>(key: K, value: HospitalInfoProfile[K]) {
    setProfile((prev) => ({ ...prev, [key]: value }))
  }

  function handleAddressChange(value: string) {
    updateField('address', value)
    setCoordinatesManuallyEdited(false)
    setCoordinateNotice('저장할 때 이 주소를 한 번 좌표로 변환합니다.')
  }

  function handleCoordinateChange(key: 'latitude' | 'longitude', value: number | null) {
    setCoordinatesManuallyEdited(true)
    updateField(key, value)
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError(null)
    setSuccess(false)
    setCoordinateNotice(null)
    setSourceRegistration([])
    try {
      const addressChanged = (profile.address ?? '').trim() !== savedAddress.trim()
      const saved = await fetchAPI<ProfileSaveResponse>(`/admin/hospitals/${hospitalId}/profile`, {
        method: 'PATCH',
        // 로고는 업로드 엔드포인트가 소유한다 — 여기서 함께 보내면 오래된 폼 값이
        // 방금 올린 파일 참조를 지운다.
        body: JSON.stringify({
          ...profilePatchPayload(profile),
          geocode_address: !coordinatesManuallyEdited,
        }),
      })
      setProfile((current) => ({
        ...current,
        latitude: saved.latitude ?? null,
        longitude: saved.longitude ?? null,
      }))
      setSavedAddress(saved.address ?? '')
      setSourceRegistration(saved.source_registration ?? [])
      if (addressChanged) {
        setCoordinateNotice(
          coordinatesManuallyEdited
            ? '고급에서 입력한 좌표를 사용했습니다.'
            : `주소에서 좌표를 한 번 변환했습니다: ${saved.latitude}, ${saved.longitude}`,
        )
      }
      setCoordinatesManuallyEdited(false)
      setSuccess(true)
      // 남은 필수 항목과 헤더 상태는 서버 판정이다 — 저장 후 다시 받는다.
      void refetchHeader()
      setTimeout(() => setSuccess(false), 3000)
    } catch (e: unknown) {
      if (!isExpectedOperatorRequestFailure(e)) throw e
      if (e instanceof ApiError && e.detail && typeof e.detail === 'object') {
        const detail = e.detail as {
          code?: string
          message?: string
          invalid_keywords?: Array<{ keyword: string; suggested_keyword?: string | null }>
        }
        if (detail.code === 'REGION_KEYWORD_MIXED') {
          const items = (detail.invalid_keywords ?? []).map((item) => (
            item.suggested_keyword
              ? `${item.keyword} → ${item.suggested_keyword}`
              : item.keyword
          ))
          setError([
            `${detail.message ?? '지역 태그와 핵심 키워드를 분리해 주세요.'}${items.length ? ` (${items.join(', ')})` : ''}`,
            `오류 코드: ${detail.code}`,
          ].join('\n'))
          return
        }
      }
      setError(profileSaveErrorMessage(e))
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

        for (const key of SCALAR_AUTOFILL_KEYS) {
          if (key in draft && isBlankScalar(prev[key])) {
            const val = draft[key]
            if (typeof val === 'string' && val.trim() !== '') {
              ;(next as Record<string, unknown>)[key] = val
              if (field_meta[key]) newAiFilled[key] = field_meta[key]
            }
          }
        }

        if ('business_hours' in draft && isBlankObject(prev.business_hours)) {
          const val = draft.business_hours
          if (val && typeof val === 'object' && !Array.isArray(val)) {
            next.business_hours = val as Record<string, string>
            if (field_meta.business_hours) newAiFilled.business_hours = field_meta.business_hours
          }
        }

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
      if (!isExpectedOperatorRequestFailure(e)) throw e
      setError(safeOperatorError('onboarding', '공식 주소를 확인한 뒤 ‘병원 정보 자동 입력’을 다시 실행하세요.'))
    } finally {
      setAutofillLoading(false)
    }
  }

  if (headerLoading && !hospital) {
    return <div className="p-4 text-slate-500 sm:p-6 lg:p-8">불러오는 중...</div>
  }

  if (!hospital) {
    return (
      <div className="p-4 sm:p-6 lg:p-8">
        <OperatorIssuePanel
          message={safeOperatorError('onboarding', '운영 화면을 다시 불러 병원 정보를 확인하세요.')}
          surface="onboarding"
          onRetry={() => void refetchHeader()}
          retryLabel="병원 정보 다시 불러오기"
        />
      </div>
    )
  }

  // 완료 판정은 서버가 한다 — 화면은 서버가 남겨 둔 항목만 세어 보여 준다.
  const missingRequirements = hospital.missing_profile_requirements ?? []
  const violationFields = new Set<string>(
    (autofillResult?.violations ?? []).map((v) => v.field),
  )

  function fieldCls(fieldKey: string, isAiFilled: boolean): string {
    if (violationFields.has(fieldKey)) return 'border-red-400 bg-red-50/40'
    if (isAiFilled) return 'border-violet-300 bg-violet-50/30'
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
      <div className="space-y-6 p-4 sm:p-6 lg:p-8">
      <form onSubmit={handleSave} className="space-y-6">
        <div className="flex flex-col items-start justify-between gap-4 sm:flex-row">
          <div>
            <h2 className="text-xl font-bold text-slate-900">병원 정보</h2>
            <p className="text-sm text-slate-600 mt-1">
              병원·원장·진료 항목·연락처와 공식 채널을 한 화면에서 채웁니다. 저장하면 나머지는 자동으로 진행됩니다.
            </p>
          </div>
          <div className="flex w-full flex-wrap items-center gap-3 sm:w-auto sm:shrink-0">
            {success && <span className="text-sm text-green-600 font-medium">저장되었습니다 ✓</span>}
            <button
              type="button"
              onClick={() => setAutofillOpen(true)}
              disabled={saving || autofillLoading}
              className="min-h-11 flex-1 px-4 py-2 text-sm font-medium text-slate-700 border border-slate-300 rounded-lg hover:bg-slate-50 disabled:opacity-50 transition-colors sm:flex-none"
            >
              병원 정보 자동 입력
            </button>
            <button
              type="submit"
              disabled={saving}
              className="min-h-11 flex-1 px-5 py-2 bg-blue-600 text-white text-sm font-medium rounded-lg hover:bg-blue-700 disabled:opacity-50 transition-colors sm:flex-none"
            >
              {saving ? '저장 중...' : '저장'}
            </button>
          </div>
        </div>

        <section
          className={`rounded-xl border px-4 py-4 sm:px-6 ${
            missingRequirements.length === 0
              ? 'border-emerald-200 bg-emerald-50'
              : 'border-amber-200 bg-amber-50'
          }`}
        >
          <p className="text-sm font-semibold text-slate-900">
            {remainingRequirementsSummary(missingRequirements)}
          </p>
          {missingRequirements.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {missingRequirements.map((item) => {
                const section = sectionForRequirement(item.key)
                return (
                  <li key={item.key}>
                    <a
                      href={`#${infoSectionAnchorId(section)}`}
                      className="inline-flex items-center gap-1 rounded-full border border-amber-300 bg-white px-2.5 py-1 text-[11px] font-medium text-amber-800 hover:border-amber-400"
                    >
                      {item.label}
                      <span className="text-amber-500">· {INFO_SECTION_TITLES[section]}</span>
                    </a>
                  </li>
                )
              })}
            </ul>
          )}
        </section>

        {error && (
          <OperatorIssuePanel message={error} surface="onboarding" />
        )}

        {autofillResult && autofillResult.violations.length > 0 && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 space-y-2">
            <p className="text-sm font-semibold text-red-800">의료광고 금지 표현이 감지되었습니다</p>
            <p className="text-xs text-red-700">해당 칸을 직접 수정해 주세요. 자동으로 제거되지 않습니다.</p>
            <ul className="space-y-1">
              {autofillResult.violations.map((v) => (
                <li key={v.field} className="text-xs text-red-700">
                  <span className="font-medium">{v.field}</span>: {v.expressions.join(', ')}
                </li>
              ))}
            </ul>
          </div>
        )}

        {autofillResult && (autofillResult.rejected_fields ?? []).length > 0 && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
            <p className="text-sm font-semibold text-amber-900">원문 근거가 부족해 자동 적용하지 않은 항목</p>
            <ul className="mt-2 space-y-1 text-xs text-amber-800">
              {(autofillResult.rejected_fields ?? []).map((item, index) => (
                <li key={`${item.field}-${index}`}>{item.field}: {item.reason}</li>
              ))}
            </ul>
          </div>
        )}

        {autofillResult && (
          <div className="bg-slate-50 border border-slate-200 rounded-lg px-4 py-3">
            {autofillResult.draft && Object.keys(autofillResult.draft).length === 0 ? (
              <p className="text-sm text-slate-600">
                온라인에서 자동으로 입력할 정보를 찾지 못했습니다. URL을 확인하거나 직접 입력해 주세요.
              </p>
            ) : (
              <p className="text-sm font-medium text-slate-700 mb-2">자동 입력 결과</p>
            )}
            <ul className="flex flex-wrap gap-x-4 gap-y-1">
              {autofillResult.sources.map((src) => (
                <li key={src.name} className="text-xs text-slate-600 flex items-center gap-1">
                  <span className={src.ok ? 'text-emerald-600 font-bold' : 'text-red-500 font-bold'}>
                    {src.ok ? '✓' : '✗'}
                  </span>
                  <span>{sourceLabel(src.name)}</span>
                  {!src.ok && src.reason && <span className="text-slate-400">({src.reason})</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        <FactsSection
          profile={profile}
          aiFilled={aiFilled}
          fieldCls={fieldCls}
          coordinateNotice={coordinateNotice}
          sourceRegistration={sourceRegistration}
          onFieldChange={updateField}
          onAddressChange={handleAddressChange}
          onCoordinateChange={handleCoordinateChange}
        />
      </form>

      <BrandSection
        hospital={hospital}
        hospitalId={hospitalId}
        onSaved={() => void refetchHeader()}
      />

      <PhotosSection hospitalId={hospitalId} hospitalName={profile.name ?? hospital.name} />

      {hospital.site_built && (
        <div id="domain-setup" className="scroll-mt-24">
          <DomainSetupPanel
            hospitalId={hospitalId}
            profile={profile}
            activationReadiness={hospital}
            onProfileChange={(patch) => setProfile((prev) => ({ ...prev, ...patch }))}
            onHeaderRefresh={() => void refetchHeader()}
          />
        </div>
      )}
      </div>
    </>
  )
}
