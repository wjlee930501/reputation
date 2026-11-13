'use client'

import { useEffect, useState } from 'react'

import { SiteLayoutPicker } from '@/app/_components/SiteLayoutPicker'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { ApiError, fetchAPI } from '@/lib/api'
import { isExpectedOperatorRequestFailure, safeOperatorError } from '@/lib/operations-journey'
import {
  isSiteLayout,
  layoutDistributionPath,
  readLayoutDistribution,
  type SiteLayout,
  type SiteLayoutDistribution,
} from '@/lib/site-layout'
import type { Hospital } from '@/types'

const COPY = ADMIN_COPY.siteLayout

/**
 * 병원 정보 화면의 사이트 레이아웃 변경. 레이아웃 하나만 따로 저장한다 — 다른 브랜드 칸의
 * 스냅샷을 함께 보내지 않는다. 공개 중인 병원은 저장 즉시 공개 페이지가 바뀌므로 한 번 더 묻는다.
 */
export function SiteLayoutSection({
  hospital,
  hospitalId,
  onSaved,
}: {
  hospital: Hospital | null
  hospitalId: string
  onSaved: () => void
}) {
  const saved = isSiteLayout(hospital?.site_layout) ? hospital.site_layout : null
  const [choice, setChoice] = useState<SiteLayout | null>(saved)
  // 사람이 고른 뒤로는 서버 값으로 덮지 않는다 — 저장 직후 헤더 재조회가 늦게 오면 그 사이에
  // 고른 새 값이 옛 값으로 되돌아간다. 저장이 끝나면 다시 서버 값을 따른다.
  const [dirty, setDirty] = useState(false)
  const [distribution, setDistribution] = useState<SiteLayoutDistribution | null>(null)
  const [distributionError, setDistributionError] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // 다른 저장 뒤 헤더가 새 값을 받아 오면, 사람이 고르는 중이 아닐 때만 따라간다.
  useEffect(() => {
    if (!dirty) setChoice(saved)
  }, [saved, dirty])

  const address = hospital?.address ?? null
  const regionKey = JSON.stringify(hospital?.region ?? [])
  useEffect(() => {
    let cancelled = false
    // 지역이 바뀌면 옛 분포를 지운다 — 새 조회가 실패했을 때 다른 지역 수를 보이지 않게.
    setDistribution(null)
    setDistributionError(false)
    fetchAPI<unknown>(
      layoutDistributionPath({ address, region: JSON.parse(regionKey) as string[], excludeHospitalId: hospitalId }),
    )
      .then((response) => {
        if (cancelled) return
        const parsed = readLayoutDistribution(response)
        setDistribution(parsed)
        setDistributionError(parsed === null)
      })
      .catch((cause: unknown) => {
        if (!isExpectedOperatorRequestFailure(cause)) throw cause
        if (!cancelled) setDistributionError(true)
      })
    return () => {
      cancelled = true
    }
  }, [address, regionKey, hospitalId])

  const live = hospital?.public_service_state?.kind !== 'not_live'
  const changed = choice !== null && choice !== saved

  async function save() {
    if (!choice) return
    setSaving(true)
    setFeedback(null)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/profile`, {
        method: 'PATCH',
        body: JSON.stringify({ site_layout: choice }),
      })
      setConfirming(false)
      setDirty(false)
      setFeedback(COPY.saved)
      onSaved()
    } catch (cause: unknown) {
      if (!isExpectedOperatorRequestFailure(cause)) throw cause
      setError(
        cause instanceof ApiError && cause.message
          ? cause.message
          : safeOperatorError('onboarding', '잠시 뒤 다시 저장해 주세요.'),
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-slate-200 p-4">
      <div>
        <h4 className="text-sm font-semibold text-slate-800">{COPY.title}</h4>
        <p className="mt-0.5 break-keep text-xs text-slate-500">{COPY.hint}</p>
        {!saved && <p className="mt-1 text-xs font-medium text-slate-700">{COPY.unset}</p>}
      </div>

      <SiteLayoutPicker
        name={`site-layout-${hospitalId}`}
        value={choice}
        onChange={(layout) => {
          setDirty(true)
          setChoice(layout)
          setConfirming(false)
          setFeedback(null)
        }}
        distribution={distribution}
        distributionError={distributionError}
        brandColor={hospital?.brand_primary_color}
        disabled={saving}
      />

      {confirming ? (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-300 bg-amber-50 p-3">
          <p className="text-sm font-medium text-amber-900">{COPY.confirmLive}</p>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="inline-flex min-h-9 items-center rounded-lg bg-blue-600 px-3 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {saving ? COPY.saving : COPY.confirmYes}
          </button>
          <button
            type="button"
            onClick={() => setConfirming(false)}
            disabled={saving}
            className="inline-flex min-h-9 items-center rounded-lg border border-slate-300 bg-white px-3 text-sm font-semibold text-slate-700"
          >
            {COPY.confirmNo}
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => (live ? setConfirming(true) : void save())}
            disabled={!changed || saving}
            className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
          >
            {saving ? COPY.saving : COPY.save}
          </button>
        </div>
      )}
      {feedback && <p className="text-sm font-semibold text-green-700">{feedback}</p>}
      {error && <p className="text-sm font-semibold text-red-700">{error}</p>}
    </div>
  )
}
