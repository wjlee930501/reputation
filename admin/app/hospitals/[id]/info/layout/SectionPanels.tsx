'use client'

import { useEffect, useState } from 'react'

import { ADMIN_COPY } from '@/lib/admin-copy'
import { fetchAPI } from '@/lib/api'
import { isExpectedOperatorRequestFailure } from '@/lib/operations-journey'
import { isRecord } from '@/lib/type-guards'
import {
  canDisable,
  canMoveDown,
  canMoveUp,
  canRetitle,
  moveSection,
  siteConfigPath,
  type SectionAlign,
  type SectionBackground,
  type SectionId,
  type SiteConfigIssue,
  type SiteSection,
} from '@/lib/site-config'

const COPY = ADMIN_COPY.siteEditor


export function sectionName(id: SectionId): string {
  return COPY.sections[id] ?? id
}

export function IssueList({ issues }: { issues: readonly SiteConfigIssue[] }) {
  if (!issues.length) return null
  return (
    <ul className="space-y-1">
      {issues.map((issue, index) => (
        <li key={`${issue.path}:${index}`} className="text-xs font-semibold text-red-700">
          {issue.message}
          {issue.words?.some((word) => !issue.message.includes(word)) ? ` (${issue.words.join(', ')})` : ''}
        </li>
      ))}
    </ul>
  )
}

/** 왼쪽 섹션 목록 — 위에서부터 화면 순서. 끄기·순서만 여기서, 나머지는 오른쪽 설정에서. */
export function SectionList({
  sections,
  selected,
  onSelect,
  onChange,
  issueCount,
  titleOf = (section) => section.title,
  disabled,
}: {
  sections: SiteSection[]
  selected: SectionId | null
  onSelect: (id: SectionId) => void
  onChange: (sections: SiteSection[]) => void
  issueCount: (id: SectionId) => number
  /** 목록에 보일 제목(화면과 같은 제목 — 기본 제목이면 병원 정보의 진료 원칙 제목). */
  titleOf?: (section: SiteSection) => string | null
  disabled: boolean
}) {
  return (
    <ol className="space-y-1.5" aria-label={COPY.sectionsTitle}>
      {sections.map((section) => {
        const attachedHost = section.attached_to
        const active = selected === section.id
        const errors = issueCount(section.id)
        return (
          <li
            key={section.id}
            className={`rounded-lg border ${active ? 'border-blue-500 ring-1 ring-blue-500' : 'border-slate-200'} ${
              attachedHost ? 'ml-5' : ''
            } ${section.enabled ? 'bg-white' : 'bg-slate-50'}`}
          >
            <div className="flex items-center gap-2 px-2.5 py-2">
              <button
                type="button"
                onClick={() => onSelect(section.id)}
                aria-pressed={active}
                aria-label={sectionName(section.id)}
                className="min-w-0 flex-1 text-left"
              >
                <span className={`block truncate text-sm font-semibold ${section.enabled ? 'text-slate-800' : 'text-slate-400 line-through'}`}>
                  {sectionName(section.id)}
                </span>
                <span className="block truncate text-[11px] text-slate-500">
                  {attachedHost
                    ? COPY.attached.replace('{host}', sectionName(attachedHost))
                    : titleOf(section) ?? ' '}
                </span>
              </button>
              {errors > 0 && (
                <span className="rounded bg-red-100 px-1.5 py-0.5 text-[11px] font-semibold text-red-700">{errors}</span>
              )}
              {canDisable(section) ? (
                <label className="flex shrink-0 items-center gap-1 text-[11px] text-slate-600">
                  <input
                    type="checkbox"
                    checked={section.enabled}
                    disabled={disabled}
                    onChange={(event) =>
                      onChange(sections.map((s) => (s.id === section.id ? { ...s, enabled: event.target.checked } : s)))
                    }
                    aria-label={`${sectionName(section.id)} ${COPY.show}`}
                  />
                  {section.enabled ? COPY.on : COPY.off}
                </label>
              ) : (
                <span className="shrink-0 text-[11px] text-slate-400">{COPY.fixed}</span>
              )}
              <div className="flex shrink-0 flex-col">
                <button
                  type="button"
                  disabled={disabled || !canMoveUp(sections, section.id)}
                  onClick={() => onChange(moveSection(sections, section.id, -1))}
                  aria-label={`${sectionName(section.id)} ${COPY.moveUp}`}
                  className="rounded px-1 text-xs leading-4 text-slate-600 hover:bg-slate-100 disabled:invisible"
                >
                  ▲
                </button>
                <button
                  type="button"
                  disabled={disabled || !canMoveDown(sections, section.id)}
                  onClick={() => onChange(moveSection(sections, section.id, 1))}
                  aria-label={`${sectionName(section.id)} ${COPY.moveDown}`}
                  className="rounded px-1 text-xs leading-4 text-slate-600 hover:bg-slate-100 disabled:invisible"
                >
                  ▼
                </button>
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

export function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string
  value: T
  options: Record<T, string>
  onChange: (value: T) => void
  disabled: boolean
}) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-xs font-semibold text-slate-700">{label}</legend>
      <div className="flex flex-wrap gap-1.5">
        {(Object.keys(options) as T[]).map((option) => (
          <button
            key={option}
            type="button"
            disabled={disabled}
            aria-pressed={value === option}
            onClick={() => onChange(option)}
            className={`min-h-9 rounded-lg border px-3 text-xs font-semibold ${
              value === option ? 'border-blue-600 bg-blue-50 text-blue-700' : 'border-slate-300 bg-white text-slate-700'
            }`}
          >
            {options[option]}
          </button>
        ))}
      </div>
    </fieldset>
  )
}

/** 고른 섹션의 설정 — 보이기·제목·배경·정렬·영문 소제목. */
export function SectionSettings({
  section,
  defaultTitle,
  placeholderTitle,
  issues,
  onChange,
  disabled,
}: {
  section: SiteSection
  defaultTitle: string | null
  /** 비웠을 때 화면에 보이는 제목(기본 제목 대신 병원 정보의 제목이 보이는 섹션). 없으면 기본 제목. */
  placeholderTitle?: string | null
  issues: readonly SiteConfigIssue[]
  onChange: (section: SiteSection) => void
  disabled: boolean
}) {
  return (
    <div className="space-y-4">
      <h4 className="text-sm font-semibold text-slate-800">{sectionName(section.id)}</h4>
      <IssueList issues={issues} />
      {canDisable(section) && (
        <label className="flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={section.enabled}
            disabled={disabled}
            onChange={(event) => onChange({ ...section, enabled: event.target.checked })}
          />
          {COPY.show}
        </label>
      )}
      {canRetitle(section) ? (
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-slate-700">{COPY.sectionTitle}</span>
          <input
            type="text"
            maxLength={40}
            value={section.title === defaultTitle ? '' : section.title ?? ''}
            placeholder={placeholderTitle ?? defaultTitle ?? ''}
            disabled={disabled}
            onChange={(event) => onChange({ ...section, title: event.target.value.trim() ? event.target.value : defaultTitle })}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm"
          />
          <span className="block text-[11px] text-slate-500">{COPY.sectionTitleHint}</span>
        </label>
      ) : (
        <p className="text-xs text-slate-500">{COPY.sectionTitleFixed}</p>
      )}
      <Choice<SectionBackground>
        label={COPY.background}
        value={section.background}
        options={COPY.backgrounds}
        onChange={(background) => onChange({ ...section, background })}
        disabled={disabled}
      />
      <Choice<SectionAlign>
        label={COPY.align}
        value={section.align}
        options={COPY.aligns}
        onChange={(align) => onChange({ ...section, align })}
        disabled={disabled}
      />
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          checked={section.label}
          disabled={disabled}
          onChange={(event) => onChange({ ...section, label: event.target.checked })}
        />
        {COPY.label}
      </label>
    </div>
  )
}

type HeroValue = {
  arrangement: 'overlay' | 'split' | 'text'
  title_size: 's' | 'm' | 'l'
  blocks: string[]
  [key: string]: unknown
}

const BLOCKS = ['today', 'phone', 'location', 'booking', 'hours'] as const

/** 첫 화면 — 배치·제목 크기·정보 블록(고른 순서대로 보인다). 정보 블록은 B 레이아웃에만 있다. */
export function HeroSettings({
  hero,
  issues,
  onChange,
  disabled,
  showBlocks = true,
}: {
  hero: HeroValue
  issues: readonly SiteConfigIssue[]
  onChange: (hero: HeroValue) => void
  disabled: boolean
  showBlocks?: boolean
}) {
  return (
    <div className="space-y-4">
      <IssueList issues={issues} />
      <Choice
        label={COPY.arrangement}
        value={hero.arrangement}
        options={COPY.arrangements}
        onChange={(arrangement) => onChange({ ...hero, arrangement })}
        disabled={disabled}
      />
      <Choice
        label={COPY.titleSize}
        value={hero.title_size}
        options={COPY.titleSizes}
        onChange={(title_size) => onChange({ ...hero, title_size })}
        disabled={disabled}
      />
      {showBlocks && (
      <fieldset className="space-y-1">
        <legend className="text-xs font-semibold text-slate-700">{COPY.blocks}</legend>
        <div className="flex flex-wrap gap-1.5">
          {BLOCKS.map((block) => {
            const position = hero.blocks.indexOf(block)
            const on = position >= 0
            return (
              <button
                key={block}
                type="button"
                disabled={disabled || (on && hero.blocks.length === 1)}
                aria-pressed={on}
                onClick={() =>
                  onChange({ ...hero, blocks: on ? hero.blocks.filter((b) => b !== block) : [...hero.blocks, block] })
                }
                className={`min-h-9 rounded-lg border px-3 text-xs font-semibold ${
                  on ? 'border-blue-600 bg-blue-50 text-blue-700' : 'border-slate-300 bg-white text-slate-600'
                }`}
              >
                {on ? `${position + 1}. ` : ''}
                {COPY.blockNames[block]}
              </button>
            )
          })}
        </div>
      </fieldset>
      )}
    </div>
  )
}

type FaqValue = {
  mode: 'auto' | 'manual'
  limit: number
  content_ids: string[]
  merge_questions: boolean
}

interface FaqCandidate {
  id: string
  question: string
}

function readCandidates(value: unknown): FaqCandidate[] | null {
  if (!isRecord(value) || !Array.isArray(value.items)) return null
  return value.items.flatMap((item) =>
    isRecord(item) && typeof item.id === 'string' && typeof item.question === 'string'
      ? [{ id: item.id, question: item.question }]
      : [],
  )
}

/** 자주 묻는 질문 — 자동/직접 고르기·개수·질문 목록과 합치기. 질문 문장은 고치지 않는다(승인 문장). */
export function FaqSettings({
  hospitalId,
  faq,
  issues,
  onChange,
  disabled,
}: {
  hospitalId: string
  faq: FaqValue
  issues: readonly SiteConfigIssue[]
  onChange: (faq: FaqValue) => void
  disabled: boolean
}) {
  const [candidates, setCandidates] = useState<FaqCandidate[] | null>(null)
  const [failed, setFailed] = useState(false)
  const manual = faq.mode === 'manual'

  useEffect(() => {
    if (!manual || candidates !== null) return
    let cancelled = false
    fetchAPI<unknown>(siteConfigPath(hospitalId, '/faq-candidates'))
      .then((value) => {
        if (cancelled) return
        const parsed = readCandidates(value)
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
  }, [manual, candidates, hospitalId])

  const byId = new Map((candidates ?? []).map((candidate) => [candidate.id, candidate]))
  const toggle = (id: string) =>
    onChange({
      ...faq,
      content_ids: faq.content_ids.includes(id)
        ? faq.content_ids.filter((item) => item !== id)
        : faq.content_ids.length < 10
          ? [...faq.content_ids, id]
          : faq.content_ids,
    })

  return (
    <div className="space-y-4">
      <IssueList issues={issues} />
      <Choice
        label={COPY.faqMode}
        value={faq.mode}
        options={COPY.faqModes}
        // 자동으로 바꾸면 고른 글을 비운다 — 서버는 자동 모드에서도 고른 글 id를 검사한다(내려간 글이 남으면 저장이 막힌다).
        onChange={(mode) => onChange({ ...faq, mode, content_ids: mode === 'auto' ? [] : faq.content_ids })}
        disabled={disabled}
      />
      {!manual && (
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-slate-700">{COPY.faqLimit}</span>
          <select
            value={faq.limit}
            disabled={disabled}
            onChange={(event) => onChange({ ...faq, limit: Number(event.target.value) })}
            className="rounded-lg border border-slate-300 px-3 py-2 text-sm"
          >
            {[3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      )}
      {manual && (
        <div className="space-y-2">
          {faq.content_ids.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-semibold text-slate-700">{COPY.faqPicked}</p>
              <ol className="list-decimal space-y-1 pl-5 text-xs text-slate-700">
                {faq.content_ids.map((id) => {
                  const candidate = byId.get(id)
                  return (
                    <li key={id}>
                      <span className="flex items-start gap-2">
                        <span className={`flex-1 break-keep ${candidate || candidates === null ? '' : 'text-red-700'}`}>
                          {candidate?.question ?? (candidates === null ? COPY.loading : COPY.faqUnavailable)}
                        </span>
                        <button
                          type="button"
                          disabled={disabled}
                          onClick={() => onChange({ ...faq, content_ids: faq.content_ids.filter((item) => item !== id) })}
                          className="shrink-0 font-semibold text-blue-700 hover:underline disabled:opacity-50"
                        >
                          {COPY.faqRemove}
                        </button>
                      </span>
                    </li>
                  )
                })}
              </ol>
            </div>
          )}
          <p className="text-xs font-semibold text-slate-700">{COPY.faqCandidates}</p>
          {failed && <p className="text-xs font-semibold text-red-700">{COPY.faqCandidatesFailed}</p>}
          {candidates !== null && candidates.length === 0 && !failed && (
            <p className="text-xs text-slate-500">{COPY.faqCandidatesEmpty}</p>
          )}
          <ul className="max-h-64 space-y-1 overflow-y-auto">
            {(candidates ?? []).map((candidate) => (
              <li key={candidate.id}>
                <label className="flex items-start gap-2 text-xs text-slate-700">
                  <input
                    type="checkbox"
                    checked={faq.content_ids.includes(candidate.id)}
                    disabled={disabled}
                    onChange={() => toggle(candidate.id)}
                    className="mt-0.5"
                  />
                  <span className="break-keep">{candidate.question}</span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          checked={faq.merge_questions}
          disabled={disabled}
          onChange={(event) => onChange({ ...faq, merge_questions: event.target.checked })}
        />
        {COPY.faqMerge}
      </label>
    </div>
  )
}
