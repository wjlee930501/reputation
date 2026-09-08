'use client'

import { useEffect, useState } from 'react'

import { ApiError, fetchAPI } from '@/lib/api'
import { brandDefaultsNotice } from '@/lib/info-sections'
import { safeOperatorError } from '@/lib/operations-journey'
import type { Hospital } from '@/types'
import { ClinicVisualForm } from './ClinicVisualForm'

interface AdvancedBrandValues {
  brand_accent_color: string
  hero_image_url: string
  hero_media_kind: string
  image_style_direction: string
}

function advancedValuesOf(hospital: Hospital | null): AdvancedBrandValues {
  return {
    brand_accent_color: hospital?.brand_accent_color ?? '',
    hero_image_url: hospital?.hero_image_url ?? '',
    hero_media_kind: hospital?.hero_media_kind ?? '',
    image_style_direction: hospital?.image_style_direction ?? '',
  }
}

/**
 * 병원 공개 페이지의 브랜드 override.
 *
 * 로고·대표색·첫 화면 문구는 비어 있어도 공개 페이지가 기본값으로 정상 노출한다.
 * 그래서 이 섹션에는 승인 버튼도 미승인 배지도 없다 — 바꾸고 싶은 값만 입력하면 된다.
 * 자주 쓰지 않는 포인트 컬러·대표 이미지·아트 디렉션은 고급으로 접어 둔다.
 */
export function BrandSection({
  hospital,
  hospitalId,
  onSaved,
}: {
  hospital: Hospital | null
  hospitalId: string
  onSaved: () => void
}) {
  const notice = brandDefaultsNotice(hospital?.visual_approval_missing ?? [])

  return (
    <section
      id="info-brand"
      className="scroll-mt-24 space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-6"
    >
      <div>
        <h3 className="text-base font-semibold text-slate-800">병원 공개 페이지 브랜드</h3>
        <p className="mt-0.5 text-xs text-slate-500">
          대표색 하나만 정하면 나머지 밝기 단계와 대비 안전 색상은 공개 페이지가 파생합니다.
          입력하지 않은 값은 기본값 그대로 나갑니다.
        </p>
      </div>

      <ClinicVisualForm
        hospital={hospital}
        hospitalId={hospitalId}
        intro={
          notice && (
            <p className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700">
              {notice}
            </p>
          )
        }
        onSaved={onSaved}
      />

      <AdvancedBrandFields hospital={hospital} hospitalId={hospitalId} onSaved={onSaved} />
    </section>
  )
}

/** 대부분의 병원은 건드리지 않는 칸. 기본값으로 두어도 공개 페이지는 정상이다. */
function AdvancedBrandFields({
  hospital,
  hospitalId,
  onSaved,
}: {
  hospital: Hospital | null
  hospitalId: string
  onSaved: () => void
}) {
  const [form, setForm] = useState<AdvancedBrandValues>(() => advancedValuesOf(hospital))
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 입력 중에는 서버 스냅샷으로 덮지 않는다 — 헤더는 다른 저장 뒤에도 새로 받아 온다.
  const serverSignature = JSON.stringify(advancedValuesOf(hospital))
  useEffect(() => {
    if (dirty) return
    setForm(JSON.parse(serverSignature) as AdvancedBrandValues)
  }, [serverSignature, dirty])

  function update<Field extends keyof AdvancedBrandValues>(field: Field, value: string) {
    setDirty(true)
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function save() {
    setSaving(true)
    setFeedback(null)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/profile`, {
        method: 'PATCH',
        body: JSON.stringify({
          brand_accent_color: form.brand_accent_color.trim() || null,
          hero_image_url: form.hero_image_url.trim() || null,
          hero_media_kind: form.hero_media_kind || null,
          image_style_direction: form.image_style_direction.trim() || null,
        }),
      })
      setDirty(false)
      setFeedback('고급 항목을 저장했습니다.')
      onSaved()
    } catch (e: unknown) {
      setError(
        e instanceof ApiError && e.message
          ? e.message
          : safeOperatorError('onboarding', '입력값을 확인한 뒤 다시 저장해 주세요.'),
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <details className="rounded-xl border border-slate-200 bg-slate-50 p-4">
      <summary className="cursor-pointer text-sm font-semibold text-slate-700">
        고급 — 포인트 컬러·대표 이미지·아트 디렉션
      </summary>
      <div className="mt-4 space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="text-sm font-medium text-slate-700">
            포인트 컬러
            <span className="mt-1.5 flex items-center gap-2">
              <input
                type="color"
                value={form.brand_accent_color || '#B79045'}
                onChange={(e) => update('brand_accent_color', e.target.value.toUpperCase())}
                className="h-10 w-12 rounded border border-slate-300 bg-white p-1"
                aria-label="포인트 컬러 선택"
              />
              <input
                type="text"
                value={form.brand_accent_color}
                onChange={(e) => update('brand_accent_color', e.target.value)}
                placeholder="#B79045"
                pattern="#[0-9A-Fa-f]{6}"
                className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm"
              />
            </span>
          </label>
          <label className="text-sm font-medium text-slate-700">
            대표 이미지 성격
            <select
              value={form.hero_media_kind}
              onChange={(e) => update('hero_media_kind', e.target.value)}
              className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
            >
              <option value="">등록 사진에서 자동 선택</option>
              <option value="VERIFIED_FACILITY">실제 병원 공간 — 장소 확인 완료</option>
              <option value="BRAND_GRAPHIC">브랜드 그래픽 — 실제 공간 아님</option>
            </select>
          </label>
        </div>
        <label htmlFor="info-hero-image-url" className="block text-sm font-medium text-slate-700">
          대표 이미지 URL
          <input
            id="info-hero-image-url"
            type="url"
            value={form.hero_image_url}
            onChange={(e) => update('hero_image_url', e.target.value)}
            placeholder="https://.../hero.jpg"
            className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
          />
        </label>
        <label htmlFor="info-image-style-direction" className="block text-sm font-medium text-slate-700">
          콘텐츠 이미지 아트 디렉션
          <textarea
            id="info-image-style-direction"
            value={form.image_style_direction}
            onChange={(e) => update('image_style_direction', e.target.value)}
            maxLength={600}
            rows={3}
            placeholder="예: 지역 가족의 일상을 돌보는 따뜻한 의원. 밝은 자연광, 아이보리와 연두, 실제 병원처럼 가장하지 않는 손그림 질감."
            className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm leading-6"
          />
          <span className="mt-1 block text-xs font-normal text-slate-500">
            콘텐츠 제목·진료 분야·원장 정보에 입력한 진료 방향을 참고해 이미지 분위기를 만듭니다.
          </span>
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {saving ? '저장 중…' : '고급 항목 저장'}
          </button>
          {feedback && <span className="text-sm font-semibold text-green-700">{feedback}</span>}
          {error && <span className="text-sm font-semibold text-red-700">{error}</span>}
        </div>
      </div>
    </details>
  )
}
