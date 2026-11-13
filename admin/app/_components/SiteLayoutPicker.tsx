'use client'

import { ADMIN_COPY } from '@/lib/admin-copy'
import {
  layoutDistributionLine,
  sameRegionBadge,
  SITE_LAYOUTS,
  type SiteLayout,
  type SiteLayoutDistribution,
} from '@/lib/site-layout'

const COPY = ADMIN_COPY.siteLayout
const HEX = /^#[0-9A-Fa-f]{6}$/

/**
 * 병원 공개 페이지 레이아웃 A/B/C 고르기. 계약 등록(필수)과 병원 정보(변경)가 같이 쓴다.
 *
 * 카드마다 정적 도식 하나와 한 줄 설명, 같은 시·도 공개 병원이 이미 쓰는 수를 보인다 —
 * 같은 지역 병원 사이트가 한 틀로 보이지 않게 하려는 것이 이 선택의 이유다(2026-11-13 결정).
 * 저장은 하지 않는다. 고른 값은 부르는 화면이 자기 요청에 싣는다.
 */
export function SiteLayoutPicker({
  name,
  value,
  onChange,
  distribution,
  distributionError = false,
  brandColor,
  disabled = false,
}: {
  name: string
  value: SiteLayout | null
  onChange: (layout: SiteLayout) => void
  distribution: SiteLayoutDistribution | null
  distributionError?: boolean
  brandColor?: string | null
  disabled?: boolean
}) {
  const brand = brandColor && HEX.test(brandColor) ? brandColor : '#334155'
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-3" role="radiogroup" aria-label={COPY.title}>
        {SITE_LAYOUTS.map((layout) => {
          const option = COPY.options[layout]
          const badge = sameRegionBadge(distribution, layout)
          const checked = value === layout
          return (
            <label
              key={layout}
              className={`flex cursor-pointer flex-col gap-2 rounded-xl border p-3 transition-colors focus-within:ring-2 focus-within:ring-blue-400 ${
                checked ? 'border-blue-600 bg-blue-50 ring-1 ring-blue-600' : 'border-slate-200 bg-white hover:border-slate-400'
              } ${disabled ? 'cursor-not-allowed opacity-60' : ''}`}
            >
              <input
                type="radio"
                name={name}
                value={layout}
                checked={checked}
                disabled={disabled}
                onChange={() => onChange(layout)}
                className="sr-only"
              />
              <LayoutThumbnail layout={layout} brand={brand} />
              <span className="text-sm font-semibold text-slate-900">{option.name}</span>
              <span className="break-keep text-xs leading-5 text-slate-600">{option.summary}</span>
              {badge && (
                <span className="self-start rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900">
                  {badge}
                </span>
              )}
            </label>
          )
        })}
      </div>
      {distribution ? (
        <p className="text-xs text-slate-600">{layoutDistributionLine(distribution)}</p>
      ) : distributionError ? (
        <p className="text-xs text-slate-500">{COPY.loadFailed}</p>
      ) : null}
    </div>
  )
}

/** 레이아웃의 첫 화면 뼈대만 그린 도식. 실제 병원 사진·문구는 쓰지 않는다. */
function LayoutThumbnail({ layout, brand }: { layout: SiteLayout; brand: string }) {
  const tint = `${brand}1A`
  const bar = 'rounded-sm bg-slate-300'
  if (layout === 'A') {
    return (
      <div aria-hidden="true" className="flex h-24 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="flex flex-1 items-center gap-2 px-2" style={{ background: tint }}>
          <div className="flex flex-1 flex-col gap-1">
            <div className="h-2.5 w-11/12 rounded-sm" style={{ background: brand }} />
            <div className="h-2.5 w-3/4 rounded-sm" style={{ background: brand }} />
            <div className={`mt-1 h-1.5 w-1/2 ${bar}`} />
            <div className="mt-1 h-2.5 w-8 rounded-full" style={{ background: brand }} />
          </div>
          <div className="h-12 w-[42%] rounded-md bg-slate-300" />
        </div>
        <div className="flex h-7 flex-col items-center justify-center gap-1">
          <div className="h-1.5 w-1/3 rounded-sm" style={{ background: brand }} />
          <div className="flex gap-1">
            {[0, 1, 2, 3].map((index) => (
              <div key={index} className="h-1.5 w-5 rounded-full border border-slate-300" />
            ))}
          </div>
        </div>
      </div>
    )
  }
  if (layout === 'B') {
    return (
      <div aria-hidden="true" className="flex h-24 flex-col gap-1.5 overflow-hidden rounded-lg border border-slate-200 bg-white p-2">
        <div className="flex flex-1 gap-1.5">
          <div className="flex flex-1 flex-col justify-center gap-1 rounded-md px-1.5" style={{ background: tint }}>
            <div className="h-2 w-5/6 rounded-sm bg-slate-500" />
            <div className="h-2 w-2/3 rounded-sm" style={{ background: brand }} />
            <div className={`h-1 w-3/4 ${bar}`} />
          </div>
          <div className="w-[45%] rounded-md bg-slate-300" />
        </div>
        <div className="flex h-4 gap-1 rounded-md px-1.5 py-1" style={{ background: brand }}>
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className="flex-1 rounded-sm bg-white/40" />
          ))}
        </div>
        <div className="mx-auto h-1.5 w-1/3 rounded-sm bg-slate-400" />
      </div>
    )
  }
  return (
    <div aria-hidden="true" className="flex h-24 flex-col overflow-hidden rounded-lg border border-slate-200 bg-white">
      <div className="relative flex flex-1 items-end justify-between bg-slate-500 px-2 pb-1.5">
        <div className="flex flex-col gap-1">
          <div className="h-2 w-16 rounded-sm bg-white" />
          <div className="h-1 w-10 rounded-sm bg-white/70" />
        </div>
        <div className="flex h-7 w-14 flex-col overflow-hidden rounded-sm bg-white">
          <div className="flex-1" />
          <div className="h-2.5" style={{ background: brand }} />
        </div>
      </div>
      <div className="flex h-8 gap-1.5 px-2 py-1.5">
        <div className="flex flex-1 flex-col gap-1">
          <div className={`h-1 w-full ${bar}`} />
          <div className={`h-1 w-full ${bar}`} />
          <div className={`h-1 w-4/5 ${bar}`} />
        </div>
        <div className="flex flex-1 flex-col gap-0.5 rounded-sm p-0.5" style={{ background: tint }}>
          <div className="h-1.5 w-full rounded-sm" style={{ background: brand }} />
          <div className="h-1 w-full rounded-sm bg-slate-300" />
          <div className="h-1 w-full rounded-sm bg-slate-300" />
        </div>
      </div>
    </div>
  )
}
