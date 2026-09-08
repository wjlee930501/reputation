'use client'

import { useId, useState } from 'react'
import { ADMIN_COPY } from '@/lib/admin-copy'
import {
  GOOGLE_CHANNEL_FIELD_HINTS,
  findDuplicateChannelUrls,
} from '@/lib/external-channel-urls'
import { INFO_SECTION_TITLES } from '@/lib/info-sections'
import type { AutofillFieldMeta } from '@/lib/api'
import type { ProfileSourceRegistration } from '@/types'

export interface Treatment {
  name: string
  description: string
}

export interface BusinessHours {
  [day: string]: string
}

/**
 * 이 화면이 편집하는 사실 필드. `Hospital`(types/index.ts)의 같은 이름 필드와
 * 짝을 이룬다 — 헤더가 받아 둔 상세 응답을 그대로 폼에 부어 쓰기 때문이다.
 */
export interface HospitalInfoProfile {
  name: string
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
  // 이 화면이 편집하지는 않지만 헤더 스냅샷을 그대로 시드로 쓰기 때문에 폼 상태에
  // 섞여 들어온다. 저장 직전에 profilePatchPayload가 걷어낸다(업로드 쪽이 소유).
  logo_url?: string | null
}

const DAYS = ['월', '화', '수', '목', '금', '토', '일']
const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

const SOURCE_LABEL_MAP: Record<string, string> = {
  homepage: '홈페이지',
  blog: '블로그',
  naver: '네이버 플레이스',
  inferred: '추론',
}

export function sourceLabel(source: string): string {
  return SOURCE_LABEL_MAP[source] ?? source
}

export function AiBadge({ meta }: { meta: AutofillFieldMeta }) {
  const pct = Math.round(meta.confidence * 100)
  return (
    <span
      title={`출처: ${sourceLabel(meta.source)} · 신뢰도 ${pct}%`}
      className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-semibold rounded-full border bg-violet-50 text-violet-700 border-violet-200 cursor-default"
    >
      AI 제안 {pct}%
    </span>
  )
}

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
              onClick={() => onChange(values.filter((item) => item !== v))}
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

function SectionShell({
  section,
  description,
  action,
  children,
}: {
  section: keyof typeof INFO_SECTION_TITLES
  description: string
  action?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <section
      id={`info-${section}`}
      className="scroll-mt-24 bg-white rounded-xl border border-slate-200 p-4 space-y-4 sm:p-6"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-slate-800">{INFO_SECTION_TITLES[section]}</h3>
          <p className="text-xs text-slate-500 mt-0.5">{description}</p>
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

/** 공식 채널 저장이 자료 자동 등록에 실패했을 때만 해당 칸 옆에 사실을 남긴다. */
function SourceRegistrationWarning({
  field,
  results,
}: {
  field: string
  results: ProfileSourceRegistration[]
}) {
  const failed = results.find((item) => item.field === field && item.status === 'FAILED')
  if (!failed) return null
  return (
    <p className="mt-1 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-800">
      주소는 저장했지만 {ADMIN_COPY.evidence}로 자동 등록하지 못했습니다.
      {failed.message ? ` (${failed.message})` : ''}
    </p>
  )
}

export interface FactsSectionProps {
  profile: Partial<HospitalInfoProfile>
  aiFilled: Record<string, AutofillFieldMeta>
  fieldCls: (fieldKey: string, isAiFilled: boolean) => string
  coordinateNotice: string | null
  sourceRegistration: ProfileSourceRegistration[]
  onFieldChange: <K extends keyof HospitalInfoProfile>(key: K, value: HospitalInfoProfile[K]) => void
  onAddressChange: (value: string) => void
  onCoordinateChange: (key: 'latitude' | 'longitude', value: number | null) => void
}

export function FactsSection({
  profile,
  aiFilled,
  fieldCls,
  coordinateNotice,
  sourceRegistration,
  onFieldChange,
  onAddressChange,
  onCoordinateChange,
}: FactsSectionProps) {
  const [weekdayCommonHours, setWeekdayCommonHours] = useState('')
  const treatments = profile.treatments ?? []
  const duplicateChannelUrlWarnings = findDuplicateChannelUrls(profile)

  function updateHours(dayKey: string, value: string) {
    onFieldChange('business_hours', { ...(profile.business_hours ?? {}), [dayKey]: value })
  }

  function applyWeekdayCommonHours() {
    onFieldChange('business_hours', {
      ...(profile.business_hours ?? {}),
      mon: weekdayCommonHours,
      tue: weekdayCommonHours,
      wed: weekdayCommonHours,
      thu: weekdayCommonHours,
      fri: weekdayCommonHours,
    })
  }

  function updateTreatment(index: number, field: keyof Treatment, value: string) {
    const next = [...treatments]
    next[index] = { ...next[index], [field]: value }
    onFieldChange('treatments', next)
  }

  function parseCoordinate(raw: string): number | null | undefined {
    const trimmed = raw.trim()
    if (trimmed === '') return null
    const parsed = Number(trimmed)
    if (!Number.isFinite(parsed)) return undefined
    return Math.round(parsed * 1_000_000) / 1_000_000
  }

  return (
    <>
      <SectionShell
        section="director"
        description="인터뷰·기고문·소개자료에서 확인한 내용을 근거로 입력합니다. 진료 철학은 출처가 분명한 문장으로 정리해 주세요."
      >
        <div>
          <label htmlFor="info-director-name" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            원장명
            {aiFilled.director_name && <AiBadge meta={aiFilled.director_name} />}
          </label>
          <input
            type="text"
            id="info-director-name"
            value={profile.director_name ?? ''}
            onChange={(e) => onFieldChange('director_name', e.target.value)}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${fieldCls('director_name', !!aiFilled.director_name)}`}
          />
        </div>
        <div>
          <label htmlFor="info-director-career" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            약력
            {aiFilled.director_career && <AiBadge meta={aiFilled.director_career} />}
          </label>
          <textarea
            id="info-director-career"
            value={profile.director_career ?? ''}
            onChange={(e) => onFieldChange('director_career', e.target.value)}
            rows={3}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none ${fieldCls('director_career', !!aiFilled.director_career)}`}
          />
        </div>
        <div>
          <label htmlFor="info-director-philosophy" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            진료 철학
            {aiFilled.director_philosophy && <AiBadge meta={aiFilled.director_philosophy} />}
          </label>
          <textarea
            id="info-director-philosophy"
            value={profile.director_philosophy ?? ''}
            onChange={(e) => onFieldChange('director_philosophy', e.target.value)}
            rows={3}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent resize-none ${fieldCls('director_philosophy', !!aiFilled.director_philosophy)}`}
          />
        </div>
      </SectionShell>

      <SectionShell
        section="treatments"
        description="시술·치료 안내와 질환 가이드 글을 자동으로 만드는 기준입니다. 실제 진료하는 항목만 입력해 주세요."
        action={
          <div className="flex shrink-0 items-center gap-2">
            {aiFilled.treatments && <AiBadge meta={aiFilled.treatments} />}
            <button
              type="button"
              onClick={() => onFieldChange('treatments', [...treatments, { name: '', description: '' }])}
              className="px-3 py-1.5 text-xs font-medium text-blue-600 border border-blue-300 rounded-lg hover:bg-blue-50 transition-colors"
            >
              + 추가
            </button>
          </div>
        }
      >
        <div className="space-y-3">
          {treatments.map((t, i) => (
            <div key={i} className="flex gap-3 items-start">
              <div className="flex-1 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <input
                  type="text"
                  value={t.name}
                  onChange={(e) => updateTreatment(i, 'name', e.target.value)}
                  placeholder="항목명 (예: 하지정맥류)"
                  aria-label={`진료 항목 ${i + 1} 항목명`}
                  className="px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
                <input
                  type="text"
                  value={t.description}
                  onChange={(e) => updateTreatment(i, 'description', e.target.value)}
                  placeholder="설명"
                  aria-label={`진료 항목 ${i + 1} 설명`}
                  className="px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                />
              </div>
              <button
                type="button"
                onClick={() => onFieldChange('treatments', treatments.filter((_, index) => index !== i))}
                aria-label={`${t.name || '진료 항목'} 제거`}
                className="mt-2 text-slate-400 hover:text-red-500 transition-colors text-lg leading-none"
              >
                ×
              </button>
            </div>
          ))}
          {treatments.length === 0 && (
            <p className="text-sm text-slate-400">진료 항목을 추가해 주세요.</p>
          )}
        </div>
      </SectionShell>

      <SectionShell
        section="contact"
        description={`${ADMIN_COPY.publicPage}와 ${ADMIN_COPY.aiExposure}에 그대로 나가는 공개 정보입니다. 실제 영업 정보와 일치하는지 확인해 주세요.`}
      >
        <div>
          <label htmlFor="info-address" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            주소
            {aiFilled.address && <AiBadge meta={aiFilled.address} />}
          </label>
          <input
            type="text"
            id="info-address"
            value={profile.address ?? ''}
            onChange={(e) => onAddressChange(e.target.value)}
            className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${fieldCls('address', !!aiFilled.address)}`}
          />
          {coordinateNotice && <p className="mt-1.5 text-xs text-slate-500">{coordinateNotice}</p>}
        </div>
        <div>
          <label htmlFor="info-phone" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
            전화번호
            {aiFilled.phone && <AiBadge meta={aiFilled.phone} />}
          </label>
          <input
            type="text"
            id="info-phone"
            value={profile.phone ?? ''}
            onChange={(e) => onFieldChange('phone', e.target.value)}
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
            <div className="rounded-lg border border-blue-100 bg-blue-50 p-3">
              <label htmlFor="info-weekday-common" className="block text-xs font-semibold text-blue-900">
                평일 공통 진료시간
              </label>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                <input
                  id="info-weekday-common"
                  type="text"
                  value={weekdayCommonHours}
                  onChange={(event) => setWeekdayCommonHours(event.target.value)}
                  placeholder="09:00 ~ 18:00 (점심 13:00 ~ 14:00)"
                  className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
                />
                <button
                  type="button"
                  onClick={applyWeekdayCommonHours}
                  disabled={!weekdayCommonHours.trim()}
                  className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                >
                  월–금 한 번에 채우기
                </button>
              </div>
              <p className="mt-1.5 text-xs text-blue-800">아래 요일별 칸에서 휴진·야간 진료 같은 예외만 수정하세요.</p>
            </div>
            <p className="text-xs font-semibold text-slate-600">요일별 예외·주말</p>
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
      </SectionShell>

      <SectionShell
        section="channels"
        description="여기에 저장한 홈페이지·블로그 주소는 자동으로 근거 자료가 되고, 지도·플레이스 주소와 좌표는 AI 답변과 지역 검색이 병원을 알아보게 합니다."
      >
        {duplicateChannelUrlWarnings.length > 0 && (
          <ul className="space-y-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
            {duplicateChannelUrlWarnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="info-website-url" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
              홈페이지 URL
              {aiFilled.website_url && <AiBadge meta={aiFilled.website_url} />}
            </label>
            <input
              type="url"
              id="info-website-url"
              value={profile.website_url ?? ''}
              onChange={(e) => onFieldChange('website_url', e.target.value)}
              className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${fieldCls('website_url', !!aiFilled.website_url)}`}
            />
            <SourceRegistrationWarning field="website_url" results={sourceRegistration} />
          </div>
          <div>
            <label htmlFor="info-blog-url" className="flex items-center gap-2 text-sm font-medium text-slate-700 mb-1.5">
              블로그 URL
              {aiFilled.blog_url && <AiBadge meta={aiFilled.blog_url} />}
            </label>
            <input
              type="url"
              id="info-blog-url"
              value={profile.blog_url ?? ''}
              onChange={(e) => onFieldChange('blog_url', e.target.value)}
              className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent ${fieldCls('blog_url', !!aiFilled.blog_url)}`}
            />
            <SourceRegistrationWarning field="blog_url" results={sourceRegistration} />
          </div>
          <div>
            <label htmlFor="info-google-business-url" className="block text-sm font-medium text-slate-700 mb-1.5">구글 병원 정보 URL</label>
            <input
              type="url"
              id="info-google-business-url"
              value={profile.google_business_profile_url ?? ''}
              onChange={(e) => onFieldChange('google_business_profile_url', e.target.value)}
              placeholder="https://business.google.com/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            <p className="mt-1 text-xs text-slate-500">{GOOGLE_CHANNEL_FIELD_HINTS.google_business_profile_url}</p>
          </div>
          <div>
            <label htmlFor="info-google-maps-url" className="block text-sm font-medium text-slate-700 mb-1.5">구글 지도 URL</label>
            <input
              type="url"
              id="info-google-maps-url"
              value={profile.google_maps_url ?? ''}
              onChange={(e) => onFieldChange('google_maps_url', e.target.value)}
              placeholder="https://maps.google.com/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            <p className="mt-1 text-xs text-slate-500">{GOOGLE_CHANNEL_FIELD_HINTS.google_maps_url}</p>
          </div>
          <div>
            <label htmlFor="info-naver-place-url" className="block text-sm font-medium text-slate-700 mb-1.5">네이버 플레이스 URL</label>
            <input
              type="url"
              id="info-naver-place-url"
              value={profile.naver_place_url ?? ''}
              onChange={(e) => onFieldChange('naver_place_url', e.target.value)}
              placeholder="https://naver.me/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
          <div>
            <label htmlFor="info-kakao-channel-url" className="block text-sm font-medium text-slate-700 mb-1.5">카카오 채널 URL</label>
            <input
              type="url"
              id="info-kakao-channel-url"
              value={profile.kakao_channel_url ?? ''}
              onChange={(e) => onFieldChange('kakao_channel_url', e.target.value)}
              placeholder="https://pf.kakao.com/..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
          </div>
        </div>
        <details className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <summary className="cursor-pointer text-sm font-semibold text-slate-700">고급 · 위도/경도 직접 수정</summary>
          <p className="mt-2 text-xs leading-5 text-slate-500">
            주소 저장 시 좌표가 자동 변환됩니다. 자동 변환이 실패했거나 지도에서 직접 확인한 경우에만 수정하세요.
          </p>
          <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="info-latitude" className="block text-sm font-medium text-slate-700 mb-1.5">위도</label>
              <input
                type="text"
                inputMode="decimal"
                id="info-latitude"
                value={profile.latitude ?? ''}
                onChange={(e) => {
                  const parsed = parseCoordinate(e.target.value)
                  if (parsed === undefined) return
                  onCoordinateChange('latitude', parsed)
                }}
                placeholder="37.497942"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <p className="mt-1 text-xs text-slate-500">지도에서 붙여 넣으면 소수점 6자리로 저장됩니다.</p>
            </div>
            <div>
              <label htmlFor="info-longitude" className="block text-sm font-medium text-slate-700 mb-1.5">경도</label>
              <input
                type="text"
                inputMode="decimal"
                id="info-longitude"
                value={profile.longitude ?? ''}
                onChange={(e) => {
                  const parsed = parseCoordinate(e.target.value)
                  if (parsed === undefined) return
                  onCoordinateChange('longitude', parsed)
                }}
                placeholder="127.027621"
                className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
              />
              <p className="mt-1 text-xs text-slate-500">지도에서 붙여 넣으면 소수점 6자리로 저장됩니다.</p>
            </div>
          </div>
        </details>
      </SectionShell>

      <SectionShell
        section="targeting"
        description={`지역·전문과목·키워드는 글 주제와 ${ADMIN_COPY.aiMentionRate} 측정 질문을 정하는 기준입니다. 경쟁 병원은 비교 ${ADMIN_COPY.monthlyReport}의 정확도를 높입니다.`}
      >
        <TagInput
          label="지역"
          values={profile.region ?? []}
          onChange={(v) => onFieldChange('region', v)}
          badge={aiFilled.region ? <AiBadge meta={aiFilled.region} /> : undefined}
        />
        <TagInput
          label="전문과목"
          values={profile.specialties ?? []}
          onChange={(v) => onFieldChange('specialties', v)}
          badge={aiFilled.specialties ? <AiBadge meta={aiFilled.specialties} /> : undefined}
        />
        <TagInput
          label="핵심 키워드"
          values={profile.keywords ?? []}
          onChange={(v) => onFieldChange('keywords', v)}
          badge={aiFilled.keywords ? <AiBadge meta={aiFilled.keywords} /> : undefined}
        />
        <TagInput
          label="경쟁 병원"
          values={profile.competitors ?? []}
          onChange={(v) => onFieldChange('competitors', v)}
          badge={aiFilled.competitors ? <AiBadge meta={aiFilled.competitors} /> : undefined}
        />
      </SectionShell>
    </>
  )
}
