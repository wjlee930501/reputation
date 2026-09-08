'use client'

import { useEffect, useRef, useState } from 'react'

import { ApiError, fetchAPI } from '@/lib/api'
import {
  clinicVisualSignature,
  clinicVisualValuesOf,
  shouldSyncFromServer,
  type ClinicVisualSource,
  type ClinicVisualValues,
} from '@/lib/clinic-visual-form-sync'
import { safeOperatorError } from '@/lib/operations-journey'
import { ClinicLogoField } from './ClinicLogoField'

/**
 * 병원 공개 페이지의 로고·대표색 1개·첫 문장·정보 우선순위를 한 폼에서 저장한다.
 *
 * 온보딩과 병원 정보 화면이 같은 칸을 쓰므로 구현은 여기 하나뿐이다. 화면마다 다른
 * 것은 위아래 안내 문구뿐이라 `intro`·`footer`로 받는다 — 온보딩은 승인 체크리스트를,
 * 정보 화면은 기본값 안내를 넣는다.
 */
export function ClinicVisualForm({
  hospital,
  hospitalId,
  intro,
  footer,
  onSaved,
}: {
  hospital: ClinicVisualSource | null
  hospitalId: string
  intro?: React.ReactNode
  footer?: React.ReactNode
  onSaved: () => void
}) {
  const [form, setForm] = useState<ClinicVisualValues>(() => clinicVisualValuesOf(hospital))
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { logoUrl, primaryColor, heroHeadline, heroDescription, accessMode } = form

  // 자료 처리 추적은 5초마다 refresh()를 돌리고, 다른 자식 폼의 저장 성공도 같은
  // refresh를 부른다. 그때마다 새 `hospital` 객체가 오므로 값이 그대로여도 참조는
  // 바뀐다. 같은 병원을 다시 불러온 것이라면 값을 기준으로 비교하고 입력 중(dirty)에는
  // 덮지 않는다. 반대로 병원 자체가 바뀌면 이전 병원의 초안은 버리고 새로 채운다.
  const serverValues = clinicVisualValuesOf(hospital)
  const serverSignature = clinicVisualSignature(serverValues)
  const syncedSignature = useRef(serverSignature)
  const syncedHospitalId = useRef<string | null>(hospitalId)

  useEffect(() => {
    const sync = shouldSyncFromServer({
      dirty,
      syncedSignature: syncedSignature.current,
      serverSignature,
      syncedHospitalId: syncedHospitalId.current,
      hospitalId,
    })
    if (!sync) return
    const switchedHospital = syncedHospitalId.current !== hospitalId
    syncedSignature.current = serverSignature
    syncedHospitalId.current = hospitalId
    setForm(serverValues)
    if (switchedHospital) setDirty(false)
    // serverValues는 serverSignature와 같은 입력에서 파생된다 — 매 렌더 새로 만들어지는
    // 객체를 의존성에 넣으면 이 이펙트가 다시 폴링마다 돌게 된다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverSignature, dirty, hospitalId])

  function update<Field extends keyof ClinicVisualValues>(
    field: Field,
    value: ClinicVisualValues[Field],
  ) {
    setDirty(true)
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function save(event: React.FormEvent) {
    event.preventDefault()
    setSaving(true)
    setFeedback(null)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/profile`, {
        method: 'PATCH',
        // logo_url은 업로드 엔드포인트가 소유한다 — 여기서 함께 보내면 화면이 들고 있는
        // 스냅샷이 방금 업로드한 자산 참조를 덮어쓴다.
        body: JSON.stringify({
          brand_primary_color: primaryColor.trim() || null,
          hero_headline: heroHeadline.trim() || null,
          hero_description: heroDescription.trim() || null,
          site_access_mode: accessMode || null,
        }),
      })
      // 저장에 성공한 뒤에야 서버 값(정규화된 결과)과 다시 동기화한다.
      setDirty(false)
      setFeedback('공개 화면 디자인을 저장했습니다. 다음 사이트 갱신부터 반영됩니다.')
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
    <form onSubmit={save} className="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
      {intro}

      <div className="grid gap-4 sm:grid-cols-2">
        <ClinicLogoField hospitalId={hospitalId} logoUrl={logoUrl} onUploaded={onSaved} />
        <label className="text-sm font-medium text-slate-700">
          대표색 1개
          <span className="mt-1.5 flex items-center gap-2">
            <input
              type="color"
              value={primaryColor || '#17365D'}
              onChange={(e) => update('primaryColor', e.target.value.toUpperCase())}
              className="h-10 w-12 rounded border border-slate-300 bg-white p-1"
              aria-label="대표색 선택"
            />
            <input
              type="text"
              value={primaryColor}
              onChange={(e) => update('primaryColor', e.target.value)}
              placeholder="#17365D"
              pattern="#[0-9A-Fa-f]{6}"
              className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm"
            />
          </span>
        </label>
      </div>

      <label className="block text-sm font-medium text-slate-700">
        첫 화면 정보 우선순위
        <select
          value={accessMode}
          onChange={(e) => update('accessMode', e.target.value)}
          className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <option value="">병원 정보로 자동 선택</option>
          <option value="urgent">당일·야간 진료형 — 시간·전화 우선</option>
          <option value="appointment">예약·방문형 — 위치·상담 우선</option>
          <option value="specialist">전문 진료형 — 의료진·진료 분야 우선</option>
        </select>
      </label>

      <label className="block text-sm font-medium text-slate-700">
        첫 화면 카피
        <textarea
          value={heroHeadline}
          onChange={(e) => update('heroHeadline', e.target.value)}
          maxLength={160}
          rows={2}
          placeholder={'예: 오늘도 문 여는 동네 주치의\n증상과 진료 정보를 방문 전에 확인하세요'}
          className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm leading-6"
        />
        <span className="mt-1 block text-xs font-normal text-slate-500">
          의료광고 금지 표현이 있으면 저장되지 않습니다.
        </span>
      </label>

      <label className="block text-sm font-medium text-slate-700">
        첫 화면 설명
        <textarea
          value={heroDescription}
          onChange={(e) => update('heroDescription', e.target.value)}
          maxLength={320}
          rows={2}
          placeholder="환자가 방문 전에 알아야 할 사실을 짧게 적어 주세요."
          className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm leading-6"
        />
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={saving}
          className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? '저장 중…' : '시각 요소 저장'}
        </button>
        {footer}
      </div>

      {dirty && !saving && (
        <p className="text-xs text-amber-700">
          저장하지 않은 변경이 있습니다. 자료 처리가 도는 동안에도 입력은 그대로 유지됩니다.
        </p>
      )}
      {feedback && <p className="text-sm font-semibold text-green-700">{feedback}</p>}
      {error && <p className="text-sm font-semibold text-red-700">{error}</p>}
    </form>
  )
}
