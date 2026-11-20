/**
 * 병원 공개 페이지 섹션 설정(레이아웃 편집기, 설계 docs/plans/2026-11-20-clinic-site-sections-design.md 3c).
 *
 * 서버 계약(`backend/app/api/admin/site_config.py`):
 * - GET은 공개 판·초안을 **완성본**(`resolved`, 기본값과 합친 것)과 **덮어쓰기 원본**(`config`) 둘 다 준다.
 * - 저장은 기본값과 **다른 칸만** 받는다(모르는 키는 422). 편집 화면은 완성본을 고치고, 보낼 때
 *   `buildOverrides`로 레이아웃 기본값과의 차이만 만든다 — 완성본을 그대로 보내면 `attached_to`·
 *   `defaults_version` 같은 칸 때문에 거절되고, 기본값이 나중에 바뀌어도 따라가지 못한다.
 * - 초안을 바꾸는 모든 요청은 지금 초안의 `{draft_id, revision}`을 보낸다(초안이 없으면 둘 다 null).
 *   다르면 409 `DRAFT_CHANGED` + 최신 초안.
 * - 섹션 제약(첫 화면 맨 앞·필수 섹션 끄기 금지·붙은 섹션 따로 옮기기 금지·제목 고정 섹션)은 서버가
 *   다시 검사한다. 화면은 같은 규칙으로 버튼을 미리 끈다(`site_defaults.py`와 같은 값 — 테스트가 대조).
 */

import { isRecord } from './type-guards.ts'
import { isSiteLayout, type SiteLayout } from './site-layout.ts'

export type SectionId =
  | 'hero'
  | 'section_index'
  | 'contents'
  | 'news'
  | 'hours'
  | 'treatments'
  | 'doctor'
  | 'care_principles'
  | 'care_flow'
  | 'gallery'
  | 'questions'
  | 'faq'
  | 'facts'
  | 'visit'

export type SectionBackground = 'default' | 'white' | 'tint' | 'band'
export type SectionAlign = 'left' | 'center'

export interface SiteSection {
  id: SectionId
  enabled: boolean
  title: string | null
  background: SectionBackground
  align: SectionAlign
  label: boolean
  attached_to: SectionId | null
}

/** 완성본. 섹션 밖 묶음(hero·faq·nav …)은 편집기 패널이 각자 모양을 안다. */
export interface ResolvedSiteConfig {
  layout?: SiteLayout
  sections: SiteSection[]
  [group: string]: unknown
}

export interface SiteConfigRow {
  id: string
  state: 'DRAFT' | 'PUBLISHED' | 'SUPERSEDED'
  version: number | null
  layout: SiteLayout
  config: Record<string, unknown>
  resolved: ResolvedSiteConfig
  defaults_version: string | null
  revision: number
  origin: 'DEFAULT' | 'EDIT' | 'RESTORE' | 'RESET'
  based_on_id: string | null
  updated_by: string | null
  updated_at: string | null
  published_by: string | null
  published_at: string | null
  change_summary: string[]
}

export interface SiteConfigHistoryEntry {
  id: string
  state: 'PUBLISHED' | 'SUPERSEDED'
  version: number
  layout: SiteLayout
  origin: string
  published_by: string | null
  published_at: string | null
  change_summary: string[]
}

export interface SiteConfigIssue {
  path: string
  code: string
  message: string
  words?: string[]
}

export interface SiteConfigState {
  site_layout: SiteLayout | null
  published: SiteConfigRow | null
  draft: SiteConfigRow | null
  defaults: Record<SiteLayout, ResolvedSiteConfig>
  defaults_version: string
  history: SiteConfigHistoryEntry[]
  warnings: SiteConfigIssue[]
  preview_enabled: boolean
}

// --- 서버와 같은 섹션 규칙(`site_defaults.py`) ---

/** 끌 수 없는 섹션 — 위치만 바꾼다(의료진 전원 표시, 결정 S3). */
export const FIXED_SECTIONS: ReadonlySet<SectionId> = new Set<SectionId>(['hero', 'facts', 'hours', 'doctor'])
/** 첫 화면은 언제나 맨 앞(h1 하나). */
export const FIRST_SECTION: SectionId = 'hero'
/** 제목을 바꿀 수 없는 섹션 — 첫 화면 h1·바로가기·병원 이름에서 만드는 기본 정보. */
export const UNTITLED_SECTIONS: ReadonlySet<SectionId> = new Set<SectionId>(['hero', 'section_index', 'facts'])

const SECTION_KEYS = ['enabled', 'title', 'background', 'align', 'label'] as const
/** 키 단위로 합치지 않고 통째로 바꾸는 사전(`site_defaults._REPLACED_DICTS`). */
const REPLACED_DICTS = new Set(['copy', 'line_breaks'])
/** 완성본에만 있고 저장 모양에는 없는 칸. */
const RESOLVED_ONLY = new Set(['layout', 'sections', 'defaults_version', 'schema_version'])

export function canDisable(section: SiteSection): boolean {
  return !FIXED_SECTIONS.has(section.id)
}

export function canRetitle(section: SiteSection): boolean {
  return !UNTITLED_SECTIONS.has(section.id)
}

/** 따로 옮길 수 있는가. 첫 화면과 붙은 섹션은 제자리다(붙은 섹션은 주인과 함께 움직인다). */
export function canMove(section: SiteSection): boolean {
  return section.id !== FIRST_SECTION && !section.attached_to
}

/** 섹션과 그 섹션에 붙은 섹션들을 한 덩어리로 묶는다(붙은 섹션은 주인 바로 뒤에 온다). */
function sectionUnits(sections: readonly SiteSection[]): SiteSection[][] {
  const units: SiteSection[][] = []
  for (const section of sections) {
    const host = section.attached_to
    const last = units[units.length - 1]
    if (host && last && (last[0].id === host || last.some((item) => item.attached_to === host))) {
      last.push(section)
    } else {
      units.push([section])
    }
  }
  return units
}

/**
 * 섹션을 한 칸 위·아래로. 붙은 섹션은 주인과 함께 움직이고, 첫 화면 앞으로는 가지 않는다.
 * 움직일 수 없으면 같은 배열을 돌려준다.
 */
export function moveSection(sections: readonly SiteSection[], id: SectionId, direction: -1 | 1): SiteSection[] {
  const units = sectionUnits(sections)
  const index = units.findIndex((unit) => unit[0].id === id)
  if (index < 0 || !canMove(units[index][0])) return [...sections]
  const target = index + direction
  if (target < 0 || target >= units.length || units[target][0].id === FIRST_SECTION) return [...sections]
  const next = [...units]
  ;[next[index], next[target]] = [next[target], next[index]]
  return next.flat()
}

export function canMoveUp(sections: readonly SiteSection[], id: SectionId): boolean {
  return moveSection(sections, id, -1).some((section, index) => section.id !== sections[index]?.id)
}

export function canMoveDown(sections: readonly SiteSection[], id: SectionId): boolean {
  return moveSection(sections, id, 1).some((section, index) => section.id !== sections[index]?.id)
}

// --- 완성본 → 덮어쓰기(저장 모양) ---

function equal(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (Array.isArray(a) || Array.isArray(b)) {
    return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((item, i) => equal(item, b[i]))
  }
  if (isRecord(a) && isRecord(b)) {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)])
    for (const key of keys) if (!equal(a[key], b[key])) return false
    return true
  }
  return false
}

/**
 * 묶음(hero·faq …) 안의 칸 하나 단위로만 비교한다 — 칸 값은 통째로 싣는다. 더 깊이 쪼개면
 * 필수 칸이 있는 작은 모양(초점 `{x, y}`)에서 한쪽만 실려 422가 된다.
 */
function groupOverride(key: string, value: unknown, base: unknown): unknown {
  if (value === null || value === undefined || equal(value, base)) return undefined
  if (!isRecord(value) || !isRecord(base) || REPLACED_DICTS.has(key)) return value
  const out: Record<string, unknown> = {}
  for (const [child, childValue] of Object.entries(value)) {
    if (childValue !== null && childValue !== undefined && !equal(childValue, base[child])) out[child] = childValue
  }
  return Object.keys(out).length ? out : undefined
}

function sectionOverrides(sections: readonly SiteSection[], base: readonly SiteSection[]): Record<string, unknown>[] {
  const byId = new Map(base.map((section) => [section.id, section]))
  const reordered = !equal(
    sections.map((section) => section.id),
    base.map((section) => section.id),
  )
  const out: Record<string, unknown>[] = []
  for (const section of sections) {
    const original = byId.get(section.id)
    const entry: Record<string, unknown> = { id: section.id }
    for (const key of SECTION_KEYS) {
      const value = section[key]
      if (value === null || value === undefined) continue
      if (original && equal(value, original[key])) continue
      entry[key] = value
    }
    // 순서가 바뀌면 모든 섹션을 싣는다 — 서버는 실린 섹션끼리만 자리를 바꾼다(`merge_sections`).
    if (reordered || Object.keys(entry).length > 1) out.push(entry)
  }
  return out
}

/** 편집한 완성본을 레이아웃 기본값과 비교해 저장할 덮어쓰기만 만든다. */
export function buildOverrides(resolved: ResolvedSiteConfig, defaults: ResolvedSiteConfig): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  const sections = sectionOverrides(resolved.sections, defaults.sections)
  if (sections.length) out.sections = sections
  for (const [key, value] of Object.entries(resolved)) {
    if (RESOLVED_ONLY.has(key)) continue
    const diff = groupOverride(key, value, defaults[key])
    if (diff !== undefined) out[key] = diff
  }
  return out
}

// --- 요청 ---

export function siteConfigPath(hospitalId: string, suffix = ''): string {
  return `/admin/hospitals/${encodeURIComponent(hospitalId)}/site-config${suffix}`
}

export interface DraftExpectation {
  draft_id: string | null
  revision: number | null
}

/** 지금 화면이 아는 초안. 초안이 없으면 '없음'을 기대한다고 보낸다. */
export function draftExpectation(draft: Pick<SiteConfigRow, 'id' | 'revision'> | null): DraftExpectation {
  return draft ? { draft_id: draft.id, revision: draft.revision } : { draft_id: null, revision: null }
}

export function discardDraftPath(hospitalId: string, draft: Pick<SiteConfigRow, 'id' | 'revision'>): string {
  const query = new URLSearchParams({ draft_id: draft.id, revision: String(draft.revision) })
  return `${siteConfigPath(hospitalId, '/draft')}?${query}`
}

export function restorePath(hospitalId: string, versionId: string): string {
  return siteConfigPath(hospitalId, `/versions/${encodeURIComponent(versionId)}/restore`)
}

// --- 응답 읽기 ---

function readSection(value: unknown): SiteSection | null {
  if (!isRecord(value) || typeof value.id !== 'string') return null
  return {
    id: value.id as SectionId,
    enabled: value.enabled !== false,
    title: typeof value.title === 'string' ? value.title : null,
    background: (typeof value.background === 'string' ? value.background : 'default') as SectionBackground,
    align: value.align === 'center' ? 'center' : 'left',
    label: value.label === true,
    attached_to: typeof value.attached_to === 'string' ? (value.attached_to as SectionId) : null,
  }
}

export function readResolved(value: unknown): ResolvedSiteConfig | null {
  if (!isRecord(value) || !Array.isArray(value.sections)) return null
  const sections = value.sections.map(readSection)
  if (sections.some((section) => section === null)) return null
  return { ...value, sections: sections as SiteSection[] }
}

function readStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

export function readRow(value: unknown): SiteConfigRow | null {
  if (!isRecord(value) || typeof value.id !== 'string' || !isSiteLayout(value.layout)) return null
  const resolved = readResolved(value.resolved)
  if (!resolved || typeof value.revision !== 'number') return null
  const text = (key: string) => (typeof value[key] === 'string' ? (value[key] as string) : null)
  return {
    id: value.id,
    state: value.state as SiteConfigRow['state'],
    version: typeof value.version === 'number' ? value.version : null,
    layout: value.layout,
    config: isRecord(value.config) ? value.config : {},
    resolved,
    defaults_version: text('defaults_version'),
    revision: value.revision,
    origin: (text('origin') ?? 'EDIT') as SiteConfigRow['origin'],
    based_on_id: text('based_on_id'),
    updated_by: text('updated_by'),
    updated_at: text('updated_at'),
    published_by: text('published_by'),
    published_at: text('published_at'),
    change_summary: readStringList(value.change_summary),
  }
}

export function readIssues(value: unknown): SiteConfigIssue[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((item) =>
    isRecord(item) && typeof item.message === 'string'
      ? [
          {
            path: typeof item.path === 'string' ? item.path : '',
            code: typeof item.code === 'string' ? item.code : '',
            message: item.message,
            ...(Array.isArray(item.words) ? { words: readStringList(item.words) } : {}),
          },
        ]
      : [],
  )
}

/** GET 응답. 모양이 다르면 null — 화면은 편집기를 열지 않고 다시 불러오기를 권한다. */
export function readSiteConfigState(value: unknown): SiteConfigState | null {
  if (!isRecord(value) || !isRecord(value.defaults)) return null
  const defaults = {} as Record<SiteLayout, ResolvedSiteConfig>
  for (const layout of ['A', 'B', 'C'] as const) {
    const resolved = readResolved(value.defaults[layout])
    if (!resolved) return null
    defaults[layout] = resolved
  }
  const published = value.published == null ? null : readRow(value.published)
  const draft = value.draft == null ? null : readRow(value.draft)
  if ((value.published != null && !published) || (value.draft != null && !draft)) return null
  const history = Array.isArray(value.history)
    ? value.history.flatMap((item) =>
        isRecord(item) && typeof item.id === 'string' && typeof item.version === 'number' && isSiteLayout(item.layout)
          ? [
              {
                id: item.id,
                state: item.state as SiteConfigHistoryEntry['state'],
                version: item.version,
                layout: item.layout,
                origin: typeof item.origin === 'string' ? item.origin : '',
                published_by: typeof item.published_by === 'string' ? item.published_by : null,
                published_at: typeof item.published_at === 'string' ? item.published_at : null,
                change_summary: readStringList(item.change_summary),
              },
            ]
          : [],
      )
    : []
  return {
    site_layout: isSiteLayout(value.site_layout) ? value.site_layout : null,
    published,
    draft,
    defaults,
    defaults_version: typeof value.defaults_version === 'string' ? value.defaults_version : '',
    history,
    warnings: readIssues(value.warnings),
    preview_enabled: value.preview_enabled === true,
  }
}

export type SiteConfigFailure =
  | { kind: 'draft_changed'; message: string; draft: SiteConfigRow | null }
  | { kind: 'invalid'; message: string; errors: SiteConfigIssue[]; warnings: SiteConfigIssue[] }
  | { kind: 'preview_disabled'; message: string }
  | { kind: 'other'; message: string }

/** 쓰기 실패를 화면이 다룰 모양으로. `detail`이 객체(칸별 오류·최신 초안)라 글자로 뭉개지 않는다. */
export function readSiteConfigFailure(status: number, message: string, detail: unknown): SiteConfigFailure {
  const code = isRecord(detail) && typeof detail.code === 'string' ? detail.code : null
  const text = isRecord(detail) && typeof detail.message === 'string' ? detail.message : message
  if (status === 409 && code === 'DRAFT_CHANGED') {
    return { kind: 'draft_changed', message: text, draft: isRecord(detail) ? readRow(detail.draft) : null }
  }
  if (status === 422 && isRecord(detail)) {
    const errors = readIssues(detail.errors)
    return { kind: 'invalid', message: text, errors: errors.length ? errors : [{ path: '', code: code ?? '', message: text }], warnings: readIssues(detail.warnings) }
  }
  if (status === 503 && code === 'SITE_PREVIEW_DISABLED') return { kind: 'preview_disabled', message: text }
  return { kind: 'other', message: text }
}

const SECTION_GROUPS = new Set(['hero', 'faq', 'care_flow', 'care_principles', 'treatments', 'doctor', 'visit'])
const PAGE_GROUPS = new Set(['nav', 'share', 'region_tags', 'type', 'article'])

/**
 * 오류·경고 경로를 화면의 어느 칸이 보일지 — 섹션 id, 페이지 전체('page'), 모르면 null(위쪽 상자).
 * 서버 경로는 `sections[i]`(저장 모양 기준 i)·`sections.{id}`(이어 보이는 묶음)·`copy.{섹션}.…`·
 * `hero.line_breaks.{칸 경로}`(진료 원칙 칸은 진료 원칙 섹션) 모양이다.
 */
export function issueOwner(path: string, overrides: Record<string, unknown>): SectionId | 'page' | null {
  // 검증 오류는 `sections[0]`, 스키마(pydantic) 오류는 `sections.0` 모양이다.
  const indexed = /^sections(?:\[(\d+)\]|\.(\d+)(?=\.|$))/.exec(path)
  if (indexed) {
    const listed = Array.isArray(overrides.sections) ? overrides.sections : []
    const entry = listed[Number(indexed[1] ?? indexed[2])]
    return isRecord(entry) && typeof entry.id === 'string' ? (entry.id as SectionId) : null
  }
  const grouped = /^sections\.([a-z_]+)/.exec(path)
  if (grouped) return grouped[1] as SectionId
  if (path.startsWith('hero.line_breaks.care_principles')) return 'care_principles'
  const copy = /^copy\.([a-z_]+)\./.exec(path)
  if (copy) return copy[1] as SectionId
  const head = /^([a-z_]+)/.exec(path)?.[1] ?? ''
  if (SECTION_GROUPS.has(head)) return head as SectionId
  if (PAGE_GROUPS.has(head)) return 'page'
  return null
}

export function issuesFor(
  issues: readonly SiteConfigIssue[],
  overrides: Record<string, unknown>,
  owner: SectionId | 'page' | null,
): SiteConfigIssue[] {
  return issues.filter((issue) => issueOwner(issue.path, overrides) === owner)
}

/** 섹션 칸만(목록 숫자·섹션 기본 설정용) — `issuesFor`의 섹션 버전. */
export function issuesForSection(
  issues: readonly SiteConfigIssue[],
  overrides: Record<string, unknown>,
  id: SectionId,
): SiteConfigIssue[] {
  return issuesFor(issues, overrides, id)
}

/**
 * 미리보기 주소. 사이트의 `/preview/{token}`은 병원 주소가 아니라 플랫폼 주소에서 열린다(토큰이 병원·초안을
 * 갖는다 — 원장 설문 `/survey/{token}`과 같다). 서버가 준 경로가 `/preview/`로 시작하지 않으면 만들지 않는다.
 */
export function previewUrl(siteHost: string, previewPath: unknown): string | null {
  if (typeof previewPath !== 'string' || !/^\/preview\/[A-Za-z0-9._~-]+$/.test(previewPath)) return null
  return `https://${siteHost}${previewPath}`
}

/**
 * 공개 페이지(사이트)가 섹션 설정을 읽는가. 3b(사이트 렌더링·미리보기 경로)가 배포되기 전에는 공개 반영이
 * 레이아웃 칸만 바꾸고 나머지 설정은 화면에 나가지 않으므로 공개 반영을 막아 둔다. 3b 배포와 함께 켠다.
 */
export const SITE_RENDERS_SITE_CONFIG = false

/**
 * 이력의 「바뀐 곳」(서버 `changed_sections` 키)을 화면 이름으로. 섹션 키(`sections.faq`)는 섹션 이름,
 * 모르는 키는 「기타 설정」 하나로 묶는다 — 영문 키가 화면에 새지 않게.
 */
export function changeLabels(
  keys: readonly string[],
  names: { sections: Record<string, string>; changeKeys: Record<string, string>; changeOther: string },
): string[] {
  const labels: string[] = []
  for (const key of keys) {
    const label = key.startsWith('sections.') && key !== 'sections.order'
      ? names.sections[key.slice('sections.'.length)] ?? names.changeOther
      : names.changeKeys[key] ?? names.changeOther
    if (!labels.includes(label)) labels.push(label)
  }
  return labels
}

// --- 줄바꿈 지정(PC·모바일, 설계 3.3) — 서버 `site_config_validation`과 같은 규칙 ---

export const LINE_MAX = 200
export const LINES_MAX = 6

/** 줄바꿈을 걸 수 있는 칸(서버 `LINE_BREAK_PATHS`와 같은 고정 목록). */
export const LINE_BREAK_PATHS = [
  'hero.headline',
  'hero.description',
  'care_principles.title',
  'care_principles.lede',
  'care_principles.cards[0].title',
  'care_principles.cards[0].body',
  'care_principles.cards[1].title',
  'care_principles.cards[1].body',
  'care_principles.cards[2].title',
  'care_principles.cards[2].body',
] as const
export type LineBreakPath = (typeof LINE_BREAK_PATHS)[number]

export interface LineBreakSourceInput {
  hero_headline?: string | null
  hero_description?: string | null
  care_principles?: {
    title?: string | null
    lede?: string | null
    cards?: ReadonlyArray<{ title?: string | null; body?: string | null } | null> | null
  } | null
}

/** 줄바꿈을 거는 칸의 지금 원문. 병원 정보에 저장되지 않은 칸(기본 문구로 나가는 칸)은 null. */
export function lineBreakSource(path: LineBreakPath, hospital: LineBreakSourceInput | null): string | null {
  if (!hospital) return null
  const pick = (value: string | null | undefined) => (value && value.trim() ? value : null)
  if (path === 'hero.headline') return pick(hospital.hero_headline)
  if (path === 'hero.description') return pick(hospital.hero_description)
  const principles = hospital.care_principles ?? null
  if (path === 'care_principles.title') return pick(principles?.title)
  if (path === 'care_principles.lede') return pick(principles?.lede)
  const match = /^care_principles\.cards\[(\d)\]\.(title|body)$/.exec(path)
  if (!match) return null
  const card = principles?.cards?.[Number(match[1])] ?? null
  return pick(card?.[match[2] as 'title' | 'body'])
}

// 파이썬 `str.split()`·`str.strip()`이 공백으로 보는 글자(`str.isspace`). JS `\s`와 다르다 —
// JS는 U+FEFF를 공백으로 보고 U+0085·U+001C~001F를 공백으로 보지 않는다.
const PY_SPACE = '\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000'
const PY_SPACES = new RegExp(`[${PY_SPACE}]+`)
const PY_EDGE_SPACES = new RegExp(`^[${PY_SPACE}]+|[${PY_SPACE}]+$`, 'g')

function pyStrip(text: string): string {
  return text.replace(PY_EDGE_SPACES, '')
}

/** 여러 줄 글을 한 문장으로(공백·줄바꿈을 공백 하나로) — 서버 `joined`와 같다. */
export function joinedText(text: string): string {
  return text.split(PY_SPACES).filter(Boolean).join(' ')
}

/** 줄 나누기 — 파이썬 `str.splitlines`와 같은 줄 경계. */
export function splitLines(value: string): string[] {
  const lines = value.split(/\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/)
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  const trimmed = lines.map(pyStrip)
  return trimmed.length ? trimmed : ['']
}

/**
 * 줄을 이으면 원문과 글자·띄어쓰기까지 같은가. 줄 경계는 원문의 공백 한 칸 자리이거나 단어 중간이어야
 * 한다 — 줄바꿈 지정이 글자를 바꾸는 통로가 되지 않게(서버 `lines_match`).
 */
export function linesMatch(value: string, source: string): boolean {
  const target = joinedText(source)
  let position = 0
  const lines = splitLines(value).map(joinedText)
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index]
    if (!line || !target.startsWith(line, position)) return false
    position += line.length
    if (index < lines.length - 1 && position < target.length && target[position] === ' ') position += 1
  }
  return position === target.length
}

export type LineBreakProblem = 'too_many' | 'mismatch' | null

export function lineBreakProblem(value: string, source: string): LineBreakProblem {
  const lines = splitLines(value)
  if (lines.length > LINES_MAX || lines.some((line) => !line || line.length > LINE_MAX)) return 'too_many'
  return linesMatch(value, source) ? null : 'mismatch'
}

// --- 섹션별 안내 문구 칸(서버 `COPY_KEYS`) ---

export const COPY_KEYS_BY_SECTION: Partial<Record<SectionId, readonly string[]>> = {
  treatments: ['treatments.note'],
  doctor: ['doctor.note'],
  contents: ['contents.note'],
  questions: ['questions.note'],
  faq: ['faq.note'],
  gallery: ['gallery.note'],
  visit: ['visit.note'],
  hours: ['hours.weekend_label'],
}
