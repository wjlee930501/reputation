'use client'

import { useEffect, useState } from 'react'

import { ADMIN_COPY } from '@/lib/admin-copy'
import { fetchAPI } from '@/lib/api'
import { isExpectedOperatorRequestFailure } from '@/lib/operations-journey'
import {
  LINES_MAX,
  lineBreakProblem,
  lineBreakSource,
  siteConfigPath,
  type LineBreakPath,
  type LineBreakSourceInput,
  type SiteConfigIssue,
  veilShown,
} from '@/lib/site-config'
import { isRecord } from '@/lib/type-guards'
import { Choice, IssueList } from './SectionPanels'

const COPY = ADMIN_COPY.siteEditor

type Group = Record<string, unknown>

const input = 'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm'
const small = 'text-xs font-semibold text-slate-700'
const hint = 'block text-[11px] text-slate-500'

function Toggle({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (value: boolean) => void; disabled: boolean }) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-700">
      <input type="checkbox" checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  )
}

/** 0~1 위치(초점)·0~0.9 비율(덮개). 서버 범위와 같다. */
function Slider({
  label,
  value,
  max = 1,
  onChange,
  disabled,
}: {
  label: string
  value: number
  max?: number
  onChange: (value: number) => void
  disabled: boolean
}) {
  return (
    <label className="block space-y-1">
      <span className={small}>
        {label} <span className="font-normal text-slate-500">{Math.round(value * 100)}%</span>
      </span>
      <input
        type="range"
        min={0}
        max={max}
        step={0.05}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Math.min(max, Math.max(0, Number(event.target.value))))}
        className="w-full"
      />
    </label>
  )
}

/** 고른 것만 순서대로 — 누르면 끝에 붙고 다시 누르면 빠진다. */
export function OrderedToggles<T extends string>({
  label,
  options,
  value,
  onChange,
  disabled,
  max,
  min = 0,
}: {
  label: string
  options: Record<T, string>
  value: readonly T[]
  onChange: (value: T[]) => void
  disabled: boolean
  max?: number
  min?: number
}) {
  return (
    <fieldset className="space-y-1">
      <legend className={small}>{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {(Object.keys(options) as T[]).map((option) => {
          const position = value.indexOf(option)
          const on = position >= 0
          const blocked = on ? value.length <= min : max !== undefined && value.length >= max
          return (
            <button
              key={option}
              type="button"
              disabled={disabled || blocked}
              aria-pressed={on}
              onClick={() => onChange(on ? value.filter((item) => item !== option) : [...value, option])}
              className={`min-h-9 rounded-lg border px-3 text-xs font-semibold disabled:opacity-50 ${
                on ? 'border-blue-600 bg-blue-50 text-blue-700' : 'border-slate-300 bg-white text-slate-600'
              }`}
            >
              {on ? `${position + 1}. ` : ''}
              {options[option]}
            </button>
          )
        })}
      </div>
    </fieldset>
  )
}

type LineBreakValue = { pc?: string | null; mobile?: string | null }

/** 한 칸의 PC·모바일 줄바꿈. 글자는 병원 정보 그대로이고 줄 나눌 자리만 정한다. */
function LineBreakField({
  path,
  hospital,
  value,
  onChange,
  disabled,
}: {
  path: LineBreakPath
  hospital: LineBreakSourceInput | null
  value: LineBreakValue | undefined
  onChange: (value: LineBreakValue | undefined) => void
  disabled: boolean
}) {
  const source = lineBreakSource(path, hospital)
  const label = COPY.lineBreakPaths[path]
  if (!source) {
    return (
      <div className="space-y-1">
        <p className={small}>{label}</p>
        <p className={hint}>{COPY.lineBreakNoSource}</p>
        {/* 원문이 지워진 뒤 남은 지정은 지울 수 있어야 한다 — 남겨 두면 다른 설정도 저장되지 않는다. */}
        {value && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => onChange(undefined)}
            className="text-xs font-semibold text-blue-700 hover:underline disabled:opacity-50"
          >
            {COPY.lineBreakClear}
          </button>
        )}
      </div>
    )
  }
  const set = (device: 'pc' | 'mobile', next: string | null) => {
    const merged = { pc: value?.pc ?? null, mobile: value?.mobile ?? null, [device]: next }
    onChange(merged.pc === null && merged.mobile === null ? undefined : merged)
  }
  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-3">
      <p className={small}>{label}</p>
      {(['pc', 'mobile'] as const).map((device) => {
        const current = value?.[device] ?? null
        const problem = current === null ? null : lineBreakProblem(current, source)
        return (
          <div key={device} className="space-y-1">
            <label className="flex items-center gap-2 text-xs text-slate-700">
              <input
                type="checkbox"
                checked={current !== null}
                disabled={disabled}
                onChange={(event) => set(device, event.target.checked ? source : null)}
              />
              {device === 'pc' ? COPY.lineBreakPc : COPY.lineBreakMobile} ·{' '}
              {current === null ? COPY.lineBreakAuto : COPY.lineBreakCustom}
            </label>
            {current !== null && (
              <>
                <textarea
                  value={current}
                  rows={Math.min(LINES_MAX, current.split('\n').length + 1)}
                  disabled={disabled}
                  onChange={(event) => set(device, event.target.value)}
                  className={`${input} font-medium`}
                  aria-invalid={problem !== null}
                />
                {problem && (
                  <p className="text-xs font-semibold text-red-700">
                    {problem === 'too_many' ? COPY.lineBreakTooMany.replace('{max}', String(LINES_MAX)) : COPY.lineBreakMismatch}
                  </p>
                )}
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}

export function LineBreaksSettings({
  paths,
  hospital,
  lineBreaks,
  issues,
  onChange,
  disabled,
}: {
  paths: readonly LineBreakPath[]
  hospital: LineBreakSourceInput | null
  lineBreaks: Record<string, LineBreakValue>
  issues: readonly SiteConfigIssue[]
  onChange: (lineBreaks: Record<string, LineBreakValue>) => void
  disabled: boolean
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-semibold text-slate-800">{COPY.lineBreaksTitle}</p>
      <p className={hint}>{COPY.lineBreaksHint}</p>
      <IssueList issues={issues} />
      {paths.map((path) => (
        <LineBreakField
          key={path}
          path={path}
          hospital={hospital}
          value={lineBreaks[path]}
          disabled={disabled}
          onChange={(next) => {
            const copy = { ...lineBreaks }
            if (next) copy[path] = next
            else delete copy[path]
            onChange(copy)
          }}
        />
      ))}
    </div>
  )
}

type Point = { x: number; y: number }

function point(value: unknown, fallback: Point): Point {
  return isRecord(value) && typeof value.x === 'number' && typeof value.y === 'number' ? { x: value.x, y: value.y } : fallback
}

export function HeroImageSettings({ hero, onChange, disabled }: { hero: Group; onChange: (hero: Group) => void; disabled: boolean }) {
  const image = isRecord(hero.image) ? hero.image : {}
  const focus = point(image.focus, { x: 0.5, y: 0.5 })
  const veil = isRecord(image.veil) ? image.veil : {}
  const opacity = typeof veil.opacity === 'number' ? veil.opacity : 0.6
  const extent = (typeof veil.extent === 'string' ? veil.extent : 'text') as 'text' | 'full' | 'none'
  const setImage = (next: Group) => onChange({ ...hero, image: { ...image, ...next } })
  return (
    <div className="space-y-3">
      <p className="text-sm font-semibold text-slate-800">{COPY.imageTitle}</p>
      {veilShown(hero).focus && (
        <>
          <Slider label={COPY.focusX} value={focus.x} onChange={(x) => setImage({ focus: { ...focus, x } })} disabled={disabled} />
          <Slider label={COPY.focusY} value={focus.y} onChange={(y) => setImage({ focus: { ...focus, y } })} disabled={disabled} />
        </>
      )}
      {/* 초점은 사진을 그리는 배치에서만, 덮개는 '사진 위 글자'에서만, 농도는 '사진 전체' 덮개에서만 그려진다. */}
      {veilShown(hero).extent && (
        <Choice
          label={COPY.veilExtent}
          value={extent}
          options={COPY.veilExtents}
          onChange={(next) => setImage({ veil: { opacity, extent: next } })}
          disabled={disabled}
        />
      )}
      {veilShown(hero).opacity && (
        <Slider label={COPY.veilOpacity} value={opacity} max={0.9} onChange={(next) => setImage({ veil: { opacity: next, extent } })} disabled={disabled} />
      )}
    </div>
  )
}

/** 진료 항목 — 설명 보이기, 모양(카드·목록). 모양은 B 레이아웃에만 있다(A 알약·C 번호 타일). */
export function TreatmentsSettings({ value, onChange, disabled }: { value: Group; onChange: (value: Group) => void; disabled: boolean }) {
  return (
    <div className="space-y-3">
      <Toggle label={COPY.treatmentsShowDescription} checked={value.show_description !== false} onChange={(show_description) => onChange({ ...value, show_description })} disabled={disabled} />
      <Choice
        label={COPY.treatmentsStyle}
        value={(value.style === 'list' ? 'list' : 'cards') as 'cards' | 'list'}
        options={COPY.treatmentsStyles}
        onChange={(style) => onChange({ ...value, style })}
        disabled={disabled}
      />
    </div>
  )
}

export function DoctorSettings({ value, onChange, disabled }: { value: Group; onChange: (value: Group) => void; disabled: boolean }) {
  const focus = point(value.photo_focus, { x: 0.5, y: 0.2 })
  return (
    <div className="space-y-3">
      <Choice
        label={COPY.doctorPhotoRatio}
        value={(typeof value.photo_ratio === 'string' ? value.photo_ratio : 'portrait') as 'portrait' | 'square' | 'landscape'}
        options={COPY.doctorPhotoRatios}
        onChange={(photo_ratio) => onChange({ ...value, photo_ratio })}
        disabled={disabled}
      />
      <Slider label={COPY.doctorPhotoFocusX} value={focus.x} onChange={(x) => onChange({ ...value, photo_focus: { ...focus, x } })} disabled={disabled} />
      <Slider label={COPY.doctorPhotoFocusY} value={focus.y} onChange={(y) => onChange({ ...value, photo_focus: { ...focus, y } })} disabled={disabled} />
      <Choice
        label={COPY.doctorTitlePosition}
        value={(value.title_position === 'below' ? 'below' : 'above') as 'above' | 'below'}
        options={COPY.doctorTitlePositions}
        onChange={(title_position) => onChange({ ...value, title_position })}
        disabled={disabled}
      />
      {/* 진료 철학 표시(`doctor.show_philosophy`)는 공개 응답에 검수된 철학 문장이 없어 숨긴다(설계 20절). */}
    </div>
  )
}

export function CarePrinciplesSettings({ value, onChange, disabled }: { value: Group; onChange: (value: Group) => void; disabled: boolean }) {
  const count = typeof value.card_count === 'number' ? value.card_count : 3
  return (
    <div className="space-y-3">
      <label className="block space-y-1">
        <span className={small}>{COPY.principlesCardCount}</span>
        <select
          value={count}
          disabled={disabled}
          onChange={(event) => onChange({ ...value, card_count: Number(event.target.value) })}
          className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
        >
          {/* 지금 저장 칸은 3개 — 4개는 사이트가 먼저 읽게 된 뒤(설계 S1·S8). */}
          {[2, 3].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </label>
      <Toggle label={COPY.principlesUseDefaultCards} checked={value.use_default_cards !== false} onChange={(use_default_cards) => onChange({ ...value, use_default_cards })} disabled={disabled} />
    </div>
  )
}

type Step = { title: string; body: string }

export function CareFlowSettings({
  value,
  defaults,
  issues,
  onChange,
  disabled,
}: {
  value: Group
  defaults: Group
  issues: readonly SiteConfigIssue[]
  onChange: (value: Group) => void
  disabled: boolean
}) {
  const steps: Step[] = Array.isArray(value.steps)
    ? value.steps.map((step) => (isRecord(step) ? { title: String(step.title ?? ''), body: String(step.body ?? '') } : { title: '', body: '' }))
    : []
  const setSteps = (next: Step[]) => onChange({ ...value, steps: next })
  return (
    <div className="space-y-3">
      <IssueList issues={issues} />
      <label className="block space-y-1">
        <span className={small}>{COPY.careFlowNote}</span>
        <input
          type="text"
          maxLength={200}
          value={typeof value.note === 'string' ? value.note : ''}
          disabled={disabled}
          onChange={(event) => onChange({ ...value, note: event.target.value.trim() ? event.target.value : defaults.note })}
          className={input}
        />
        <span className={hint}>{COPY.copyHint}</span>
      </label>
      <fieldset className="space-y-2">
        <legend className={small}>{COPY.careFlowSteps}</legend>
        {steps.map((step, index) => (
          <div key={index} className="space-y-1.5 rounded-lg border border-slate-200 p-2.5">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-slate-500">{index + 1}</span>
              <input
                type="text"
                maxLength={30}
                value={step.title}
                placeholder={COPY.careFlowStepTitle}
                aria-label={`${index + 1} ${COPY.careFlowStepTitle}`}
                disabled={disabled}
                onChange={(event) => setSteps(steps.map((s, i) => (i === index ? { ...s, title: event.target.value } : s)))}
                className={input}
              />
              <button
                type="button"
                disabled={disabled || steps.length <= 2}
                onClick={() => setSteps(steps.filter((_, i) => i !== index))}
                className="shrink-0 text-xs font-semibold text-blue-700 hover:underline disabled:opacity-40"
              >
                {COPY.careFlowRemoveStep}
              </button>
            </div>
            <textarea
              maxLength={200}
              rows={2}
              value={step.body}
              placeholder={COPY.careFlowStepBody}
              aria-label={`${index + 1} ${COPY.careFlowStepBody}`}
              disabled={disabled}
              onChange={(event) => setSteps(steps.map((s, i) => (i === index ? { ...s, body: event.target.value } : s)))}
              className={input}
            />
          </div>
        ))}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={disabled || steps.length >= 6}
            onClick={() => setSteps([...steps, { title: '', body: '' }])}
            className="inline-flex min-h-9 items-center rounded-lg border border-slate-300 bg-white px-3 text-xs font-semibold text-slate-700 disabled:opacity-50"
          >
            {COPY.careFlowAddStep}
          </button>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onChange({ ...value, steps: defaults.steps })}
            className="inline-flex min-h-9 items-center rounded-lg border border-slate-300 bg-white px-3 text-xs font-semibold text-slate-700 disabled:opacity-50"
          >
            {COPY.careFlowReset}
          </button>
        </div>
      </fieldset>
    </div>
  )
}

export function VisitSettings({ value, onChange, disabled }: { value: Group; onChange: (value: Group) => void; disabled: boolean }) {
  const first = Array.isArray(value.order) && value.order[0] === 'transit' ? 'transit' : 'parking'
  return (
    <div className="space-y-3">
      <Choice
        label={COPY.visitOrder}
        value={first as 'parking' | 'transit'}
        options={COPY.visitOrders}
        onChange={(next) => onChange({ ...value, order: next === 'transit' ? ['transit', 'parking'] : ['parking', 'transit'] })}
        disabled={disabled}
      />
      <Toggle label={COPY.visitShowTransit} checked={value.show_transit !== false} onChange={(show_transit) => onChange({ ...value, show_transit })} disabled={disabled} />
    </div>
  )
}

/** 섹션 아래 안내 문장 덮어쓰기. 비우면 그 키를 지워 공통 문구로 돌아간다(`copy`는 통째로 저장). */
export function CopyNotes({
  keys,
  copy,
  issues,
  onChange,
  disabled,
}: {
  keys: readonly string[]
  copy: Record<string, string>
  issues: readonly SiteConfigIssue[]
  onChange: (copy: Record<string, string>) => void
  disabled: boolean
}) {
  return (
    <div className="space-y-3">
      <IssueList issues={issues} />
      {keys.map((key) => (
        <label key={key} className="block space-y-1">
          <span className={small}>{COPY.copyKeys[key as keyof typeof COPY.copyKeys] ?? key}</span>
          <input
            type="text"
            maxLength={200}
            value={copy[key] ?? ''}
            disabled={disabled}
            onChange={(event) => {
              const next = { ...copy }
              if (event.target.value.trim()) next[key] = event.target.value
              else delete next[key]
              onChange(next)
            }}
            className={input}
          />
          <span className={hint}>{COPY.copyHint}</span>
        </label>
      ))}
    </div>
  )
}

interface ShareCandidate {
  id: string
  title: string
  file_access_url: string
}

function readShareCandidates(value: unknown): ShareCandidate[] | null {
  if (!isRecord(value) || !Array.isArray(value.items)) return null
  return value.items.flatMap((item) =>
    isRecord(item) && typeof item.id === 'string' && typeof item.title === 'string' && typeof item.file_access_url === 'string'
      ? [{ id: item.id, title: item.title, file_access_url: item.file_access_url }]
      : [],
  )
}

/** 페이지 전체 — 상단 메뉴·모바일 하단 바·지역 이름·공유·글자 크기·글 화면 보조 칸. */
export function PageWideSettings({
  hospitalId,
  resolved,
  issues,
  onChange,
  disabled,
}: {
  hospitalId: string
  resolved: Group
  issues: readonly SiteConfigIssue[]
  onChange: (patch: Group) => void
  disabled: boolean
}) {
  const nav = isRecord(resolved.nav) ? resolved.nav : {}
  const region = isRecord(resolved.region_tags) ? resolved.region_tags : {}
  const share = isRecord(resolved.share) ? resolved.share : {}
  const type = isRecord(resolved.type) ? resolved.type : {}
  const article = isRecord(resolved.article) ? resolved.article : {}
  const [candidates, setCandidates] = useState<ShareCandidate[] | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchAPI<unknown>(siteConfigPath(hospitalId, '/share-candidates'))
      .then((value) => {
        if (cancelled) return
        const parsed = readShareCandidates(value)
        setCandidates(parsed ?? [])
        setFailed(parsed === null)
      })
      .catch((cause: unknown) => {
        if (!isExpectedOperatorRequestFailure(cause)) throw cause
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [hospitalId])

  const navItems = (Array.isArray(nav.items) ? nav.items : []) as (keyof typeof COPY.navNames)[]
  const mobileBar = (Array.isArray(nav.mobile_bar) ? nav.mobile_bar : []) as (keyof typeof COPY.mobileBarNames)[]
  const selectedImage = typeof share.image_asset_id === 'string' ? share.image_asset_id : null

  return (
    <div className="space-y-5">
      <IssueList issues={issues} />
      <OrderedToggles label={COPY.navItems} options={COPY.navNames} value={navItems} max={7} onChange={(items) => onChange({ nav: { ...nav, items } })} disabled={disabled} />
      <OrderedToggles label={COPY.mobileBar} options={COPY.mobileBarNames} value={mobileBar} max={4} onChange={(mobile_bar) => onChange({ nav: { ...nav, mobile_bar } })} disabled={disabled} />
      {/* 진료 지역 표기(`region_tags`)는 병원 화면이 아직 그리지 않아 숨긴다(설계 20절). */}
      <fieldset className="space-y-2">
        <legend className={small}>{COPY.shareImage}</legend>
        <span className={hint}>{COPY.shareImageHint}</span>
        {failed && <p className="text-xs font-semibold text-red-700">{COPY.shareImagesFailed}</p>}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <label className={`flex min-h-20 items-center justify-center rounded-lg border p-2 text-xs ${selectedImage === null ? 'border-blue-600 bg-blue-50 font-semibold text-blue-700' : 'border-slate-300 text-slate-600'}`}>
            <input type="radio" name={`share-${hospitalId}`} className="sr-only" checked={selectedImage === null} disabled={disabled} onChange={() => onChange({ share: { ...share, image_asset_id: null } })} />
            {COPY.shareImageNone}
          </label>
          {(candidates ?? []).map((candidate) => (
            <label key={candidate.id} className={`block overflow-hidden rounded-lg border text-xs ${selectedImage === candidate.id ? 'border-blue-600 ring-1 ring-blue-600' : 'border-slate-300'}`}>
              <input type="radio" name={`share-${hospitalId}`} className="sr-only" checked={selectedImage === candidate.id} disabled={disabled} onChange={() => onChange({ share: { ...share, image_asset_id: candidate.id } })} />
              {/* eslint-disable-next-line @next/next/no-img-element -- 같은 출처의 Admin 파일 경로(사진 목록 화면과 같다) */}
              <img src={candidate.file_access_url} alt="" className="h-20 w-full object-cover" loading="lazy" />
              <span className="block truncate px-2 py-1 text-slate-700">{candidate.title}</span>
            </label>
          ))}
        </div>
        {candidates !== null && candidates.length === 0 && !failed && <p className={hint}>{COPY.shareImagesEmpty}</p>}
        {selectedImage && candidates !== null && !candidates.some((c) => c.id === selectedImage) && (
          <p className="text-xs font-semibold text-red-700">{COPY.shareImageUnavailable}</p>
        )}
      </fieldset>
      <label className="block space-y-1">
        <span className={small}>{COPY.shareDescription}</span>
        <textarea
          maxLength={160}
          rows={2}
          value={typeof share.description === 'string' ? share.description : ''}
          disabled={disabled}
          onChange={(event) => onChange({ share: { ...share, description: event.target.value.trim() ? event.target.value : null } })}
          className={input}
        />
        <span className={hint}>{COPY.shareDescriptionHint}</span>
      </label>
      {/* 본문 글자 크기(`type.body_size` — A·C 글자가 고정 크기라 일부만 커진다)와 글 보조 칸 위치(`article.aside`)는 숨긴다(설계 20절). */}
    </div>
  )
}
