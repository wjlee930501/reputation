/**
 * 병원 공개 페이지 섹션 설정(레이아웃 편집기) — 공개 응답의 `site_config`를 화면 계획으로 바꾼다.
 *
 * 계약(docs/plans/2026-11-20-clinic-site-sections-design.md 6절):
 * - `site_config`가 null이면 지금 화면 그대로다(I6). 이 모듈의 기본값(`DEFAULT_SECTION_ORDER` 등)은
 *   지금 마크업의 순서이고, 백엔드 `site_defaults.defaults(layout)`와 같다
 *   (`site-config-defaults.json` — 백엔드 테스트가 그 파일을 기본값과, 사이트 테스트가 이 모듈을 그 파일과 대조한다).
 * - 설정은 표현만 바꾼다. 구조화 정보(JSON-LD)·llms.txt는 이 모듈을 읽지 않는다(I1).
 * - '켜짐'은 '보여도 됨'이다 — 데이터가 없어 숨는 기존 조건(진료 항목 없음·글 적음 등)은 그대로다.
 * - 끌 수 없는 섹션(첫 화면·기본 정보·진료시간·의료진)은 설정이 꺼도 켠다(백엔드 검증과 같은 규칙 —
 *   의료진은 공개 조건을 통과한 전원을 보인다, I4).
 * - 형식이 틀리거나 모르는 판의 설정은 통째로 버린다(null) — 화면을 깨지 않고 지금 화면으로 떨어진다.
 *
 * 이 PR(3b ①)이 쓰는 칸: 섹션 순서·켜기·제목, 섹션 안내 문장(`copy.*.note`), 진료 원칙 카드 수,
 * 진료 과정 안내·단계, FAQ 자동·직접, 상단 메뉴·모바일 하단 바, 공유 이미지·설명. 첫 화면·배경·정렬·
 * 영문 소제목·줄바꿈 같은 모양 칸은 다음 PR이다(받아 두기만 한다).
 */

import type { ContentSummary } from './api.ts'
import type { ClinicLayout } from './clinic-layout.ts'
import { CARE_FLOW_NOTE, CARE_STEPS } from './clinic-template-copy.ts'
import { selectFaqEntries, type FaqEntry } from './schema.ts'

export const SITE_CONFIG_SCHEMA_VERSION = 1

export type SiteSectionId =
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

export type SiteNavItem = 'treatments' | 'visit' | 'doctor' | 'contents' | 'faq' | 'website' | 'call'
export type SiteMobileBarItem = 'call' | 'hours' | 'guide' | 'map'

const SECTION_IDS: ReadonlySet<string> = new Set<SiteSectionId>([
  'hero',
  'section_index',
  'contents',
  'news',
  'hours',
  'treatments',
  'doctor',
  'care_principles',
  'care_flow',
  'gallery',
  'questions',
  'faq',
  'facts',
  'visit',
])
const NAV_ITEMS: ReadonlySet<string> = new Set<SiteNavItem>([
  'treatments',
  'visit',
  'doctor',
  'contents',
  'faq',
  'website',
  'call',
])
const MOBILE_BAR_ITEMS: ReadonlySet<string> = new Set<SiteMobileBarItem>(['call', 'hours', 'guide', 'map'])

/** 레이아웃별 홈 섹션 기본 순서 = 지금 마크업 순서. 레이아웃을 고르지 않은 병원(null)은 B와 같다. */
export const DEFAULT_SECTION_ORDER: Readonly<Record<ClinicLayout, readonly SiteSectionId[]>> = {
  B: [
    'hero',
    'section_index',
    'treatments',
    'doctor',
    'care_principles',
    'contents',
    'questions',
    'faq',
    'care_flow',
    'gallery',
    'facts',
    'visit',
  ],
  A: ['hero', 'treatments', 'doctor', 'care_principles', 'hours', 'contents', 'faq', 'care_flow', 'gallery', 'facts'],
  C: ['hero', 'news', 'hours', 'treatments', 'doctor', 'care_principles', 'care_flow', 'questions', 'faq', 'gallery', 'facts'],
}

/**
 * 레이아웃별 섹션 기본 제목(백엔드 `site_defaults`의 섹션 표와 같은 원문). 설정 제목이 이 값과 같으면
 * 바꾼 것이 아니다 — 그 섹션의 지금 제목(병원 정보가 바꾼 진료 원칙 제목, A안 '오늘(월) … 진료' 같은
 * 그날 문장)을 그대로 쓴다. 제목이 없는 섹션(첫 화면·바로가기·기본 정보)은 null.
 */
const COMMON_TITLES = {
  treatments: '진료 영역',
  doctor: '의료진 소개',
  care_principles: '증상의 원인부터 확인합니다',
  care_flow: '첫 방문부터 경과 관리까지, 진료 4단계',
  questions: '자주 확인하는 질문',
  faq: '자주 묻는 질문과 답변 요약',
  gallery: '병원 공간',
} as const
export const DEFAULT_SECTION_TITLES: Readonly<Record<ClinicLayout, Partial<Record<SiteSectionId, string>>>> = {
  B: { ...COMMON_TITLES, contents: '먼저 정리한 글', visit: '오시는 길·방문 안내' },
  A: { ...COMMON_TITLES, hours: '오늘 진료', contents: '자주 확인하는 질문' },
  C: { ...COMMON_TITLES, news: '최근 의료 정보', hours: '진료시간' },
}

/** 기본 꺼짐 — 바로가기 메뉴는 2026-11-20 결정 12a로 뺐다(편집기에서 다시 켤 수 있다). */
export const DEFAULT_DISABLED_SECTIONS: ReadonlySet<SiteSectionId> = new Set<SiteSectionId>(['section_index'])

/** 끌 수 없는 섹션(위치만 바뀐다). */
export const FIXED_SECTIONS: ReadonlySet<SiteSectionId> = new Set<SiteSectionId>(['hero', 'facts', 'hours', 'doctor'])

export const DEFAULT_NAV_ITEMS: readonly SiteNavItem[] = ['treatments', 'visit', 'doctor', 'contents', 'website', 'call']
export const DEFAULT_MOBILE_BAR: readonly SiteMobileBarItem[] = ['call', 'hours', 'guide', 'map']
/** 지금 홈 FAQ 상한(`schema.ts` FAQ_MAX_ITEMS)과 같다. */
export const DEFAULT_FAQ_LIMIT = 10

export interface SiteSectionConfig {
  id: SiteSectionId
  enabled: boolean
  /** 화면 제목. null이면 그 섹션의 지금 제목(병원 정보·공통 문구). */
  title: string | null
}

export interface SiteCareStep {
  title: string
  body: string
}

export interface SiteFaqEntry {
  id: string
  question: string
  answer: string
}

export interface ClinicSiteConfig {
  layout: ClinicLayout
  version: number | null
  sections: SiteSectionConfig[]
  /** 섹션 안내 문장 덮어쓰기(`treatments.note` 등). 없는 키는 지금 문장. */
  copy: Record<string, string>
  carePrinciples: { cardCount: number | null }
  careFlow: { note: string | null; steps: SiteCareStep[] | null }
  faq: { mode: 'auto' | 'manual'; limit: number; entries: SiteFaqEntry[] | null }
  nav: { items: SiteNavItem[]; mobileBar: SiteMobileBarItem[] }
  share: { imageAssetId: string | null; description: string | null }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function itemsOf<T extends string>(value: unknown, allowed: ReadonlySet<string>, fallback: readonly T[]): T[] {
  if (!Array.isArray(value)) return [...fallback]
  const seen = new Set<string>()
  const out: T[] = []
  for (const item of value) {
    if (typeof item === 'string' && allowed.has(item) && !seen.has(item)) {
      seen.add(item)
      out.push(item as T)
    }
  }
  return out
}

/**
 * 공개 응답의 `site_config` → 화면 설정. 형식이 틀리거나 판이 다르거나 병원의 지금 레이아웃과 다르면
 * null(지금 화면). 백엔드가 이미 공개용으로 정리한 값이지만, 사이트는 받은 값을 다시 좁혀 쓴다.
 */
export function normalizeSiteConfig(value: unknown, siteLayout: ClinicLayout | null): ClinicSiteConfig | null {
  if (!isRecord(value)) return null
  if (value.schema_version !== SITE_CONFIG_SCHEMA_VERSION) return null
  const layout = value.layout
  if ((layout !== 'A' && layout !== 'B' && layout !== 'C') || layout !== siteLayout) return null
  if (!Array.isArray(value.sections)) return null

  const known = new Set(DEFAULT_SECTION_ORDER[layout])
  const seen = new Set<string>()
  const sections: SiteSectionConfig[] = []
  for (const raw of value.sections) {
    if (!isRecord(raw) || typeof raw.id !== 'string' || !SECTION_IDS.has(raw.id)) return null
    const id = raw.id as SiteSectionId
    if (!known.has(id) || seen.has(id)) continue
    seen.add(id)
    const title = text(raw.title)
    sections.push({
      id,
      enabled: FIXED_SECTIONS.has(id) ? true : raw.enabled !== false,
      title: title && title !== DEFAULT_SECTION_TITLES[layout][id] ? title : null,
    })
  }
  // 설정에 없는 섹션은 기본 자리·기본값(서버 완성본은 언제나 전부 싣는다 — 옛 응답 대비).
  DEFAULT_SECTION_ORDER[layout].forEach((id, index) => {
    if (seen.has(id)) return
    sections.splice(Math.min(index, sections.length), 0, {
      id,
      enabled: !DEFAULT_DISABLED_SECTIONS.has(id),
      title: null,
    })
  })
  // 첫 화면은 언제나 맨 앞이다(h1 하나).
  const heroIndex = sections.findIndex((section) => section.id === 'hero')
  if (heroIndex > 0) sections.unshift(...sections.splice(heroIndex, 1))

  const copy: Record<string, string> = {}
  if (isRecord(value.copy)) {
    for (const [key, raw] of Object.entries(value.copy)) {
      const note = text(raw)
      if (note) copy[key] = note
    }
  }

  const principles = isRecord(value.care_principles) ? value.care_principles : {}
  const cardCount =
    typeof principles.card_count === 'number' && Number.isInteger(principles.card_count) && principles.card_count >= 1
      ? principles.card_count
      : null

  const flow = isRecord(value.care_flow) ? value.care_flow : {}
  let steps: SiteCareStep[] | null = null
  if (Array.isArray(flow.steps)) {
    const parsed = flow.steps
      .map((step) => (isRecord(step) ? { title: text(step.title), body: text(step.body) } : null))
      .filter((step): step is SiteCareStep => Boolean(step?.title && step.body))
    steps = parsed.length > 0 ? parsed : null
  }

  const faqRaw = isRecord(value.faq) ? value.faq : {}
  const mode = faqRaw.mode === 'manual' ? 'manual' : 'auto'
  const limit =
    typeof faqRaw.limit === 'number' && Number.isInteger(faqRaw.limit) && faqRaw.limit > 0
      ? Math.min(faqRaw.limit, DEFAULT_FAQ_LIMIT)
      : DEFAULT_FAQ_LIMIT
  let entries: SiteFaqEntry[] | null = null
  if (mode === 'manual') {
    entries = (Array.isArray(faqRaw.entries) ? faqRaw.entries : [])
      .map((entry) =>
        isRecord(entry) && typeof entry.id === 'string'
          ? { id: entry.id, question: text(entry.question), answer: text(entry.answer) }
          : null,
      )
      .filter((entry): entry is SiteFaqEntry => Boolean(entry?.question && entry.answer))
  }

  const nav = isRecord(value.nav) ? value.nav : {}
  const share = isRecord(value.share) ? value.share : {}

  return {
    layout,
    version: typeof value.version === 'number' ? value.version : null,
    sections,
    copy,
    carePrinciples: { cardCount },
    careFlow: { note: text(flow.note), steps },
    faq: { mode, limit, entries },
    nav: {
      items: itemsOf<SiteNavItem>(nav.items, NAV_ITEMS, DEFAULT_NAV_ITEMS),
      mobileBar: itemsOf<SiteMobileBarItem>(nav.mobile_bar, MOBILE_BAR_ITEMS, DEFAULT_MOBILE_BAR),
    },
    share: { imageAssetId: text(share.image_asset_id), description: text(share.description) },
  }
}

/** 홈 화면 계획 — 레이아웃 컴포넌트는 이 순서대로 그리고, 제목·안내만 여기서 묻는다. */
export interface HomeSectionPlan {
  /** 켜진 섹션, 화면 순서. */
  order: SiteSectionId[]
  enabled(id: SiteSectionId): boolean
  /** 설정이 바꾼 제목, 없으면 `fallback`(지금 제목). */
  title(id: SiteSectionId, fallback: string): string
  /** 설정이 바꾼 안내 문장, 없으면 `fallback`. */
  note(id: SiteSectionId, fallback: string): string
  note(id: SiteSectionId, fallback: string | null): string | null
  /** 설정이 바꾼 제목만(없으면 null) — 제목을 스스로 정하는 컴포넌트에 넘긴다. */
  customTitle(id: SiteSectionId): string | null
  /** 설정이 바꾼 안내 문장만(없으면 null). */
  customNote(id: SiteSectionId): string | null
  /** 두 섹션이 켜진 채 바로 이어 나오는가(묶음 앵커 판단 — A안 질문 목록+FAQ). */
  adjacent(first: SiteSectionId, second: SiteSectionId): boolean
}

export function buildHomeSectionPlan(layout: ClinicLayout | null, config: ClinicSiteConfig | null): HomeSectionPlan {
  const sections: SiteSectionConfig[] = config
    ? config.sections
    : DEFAULT_SECTION_ORDER[layout ?? 'B'].map((id) => ({
        id,
        enabled: !DEFAULT_DISABLED_SECTIONS.has(id),
        title: null,
      }))
  const order = sections.filter((section) => section.enabled).map((section) => section.id)
  const byId = new Map(sections.map((section) => [section.id, section]))
  const copy = config?.copy ?? {}
  function note(id: SiteSectionId, fallback: string): string
  function note(id: SiteSectionId, fallback: string | null): string | null
  function note(id: SiteSectionId, fallback: string | null): string | null {
    return copy[`${id}.note`] ?? fallback
  }
  return {
    order,
    enabled: (id) => order.includes(id),
    title: (id, fallback) => byId.get(id)?.title ?? fallback,
    note,
    customTitle: (id) => byId.get(id)?.title ?? null,
    customNote: (id) => copy[`${id}.note`] ?? null,
    adjacent: (first, second) => {
      const index = order.indexOf(first)
      return index >= 0 && order[index + 1] === second
    },
  }
}

/**
 * 홈 FAQ 항목 — 화면 FAQ 섹션·FAQPage·질문 목록 중복 제거가 함께 쓰는 배열 하나(I2·I3).
 * 설정이 없으면 지금 규칙(`selectFaqEntries`, 같은 문장 1회, 10개)이다. FAQ 섹션이 꺼지면 빈 배열이고
 * (FAQPage도 없다), 직접 고르기는 서버가 지금 공개된 글에서 다시 읽은 승인 질문·답을 고른 순서대로 쓴다
 * — 고른 글이 모두 내려가면 빈 배열이다(다른 글로 채우지 않는다).
 */
export function resolveHomeFaqEntries(
  contents: ContentSummary[],
  hospitalRootUrl: string,
  config: ClinicSiteConfig | null,
  plan: HomeSectionPlan,
): FaqEntry[] {
  if (!config) return selectFaqEntries(contents, hospitalRootUrl)
  if (!plan.enabled('faq')) return []
  if (config.faq.mode === 'manual') {
    return (config.faq.entries ?? []).slice(0, config.faq.limit).map((entry) => ({
      id: entry.id,
      question: entry.question,
      answer: entry.answer,
      url: `${hospitalRootUrl}/contents/${entry.id}`,
    }))
  }
  return selectFaqEntries(contents, hospitalRootUrl, config.faq.limit)
}

export interface CareFlowStepView {
  label: string
  /** 기본 단계의 보조 문구('첫 방문 시' 등). 직접 쓴 단계에는 없다. */
  aux: string | null
  title: string
  body: string
}

/**
 * 진료 과정 안내 문장과 단계. 설정 단계가 기본 단계와 글자까지 같으면 지금 단계(보조 문구 포함)를
 * 그대로 쓰고, 바꾼 단계는 번호·제목·설명만 보인다.
 */
export function resolveCareFlow(config: ClinicSiteConfig | null): { note: string; steps: CareFlowStepView[] } {
  const note = config?.careFlow.note ?? CARE_FLOW_NOTE
  const custom = config?.careFlow.steps
  if (!custom) return { note, steps: CARE_STEPS.map((step) => ({ ...step })) }
  return {
    note,
    steps: custom.map((step, index) => {
      const base = CARE_STEPS[index]
      if (base && base.title === step.title && base.body === step.body) return { ...base }
      return { label: String(index + 1).padStart(2, '0'), aux: null, title: step.title, body: step.body }
    }),
  }
}

/** 진료 원칙 카드 — 설정이 카드 수를 줄였으면 앞에서부터 그만큼만. */
export function limitCarePrincipleCards<T>(cards: readonly T[], config: ClinicSiteConfig | null): T[] {
  const count = config?.carePrinciples.cardCount
  return count ? cards.slice(0, count) : [...cards]
}
