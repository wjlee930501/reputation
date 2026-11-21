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
 * 3b ①이 쓰는 칸: 섹션 순서·켜기·제목, 섹션 안내 문장(`copy.*.note`), 진료 원칙 카드 수,
 * 진료 과정 안내·단계, FAQ 자동·직접, 상단 메뉴·모바일 하단 바, 공유 이미지·설명.
 * ③-2a가 더한 칸(B안만): 첫 화면 정보 띠 칸(`hero.blocks`), 진료 항목 모양·설명(`treatments`).
 * ③-2b가 더한 칸(A·B·C): 의료진 사진 비율·초점, 직함 위치(`doctor`).
 * ③-2c가 더한 칸(A·B·C): 섹션 제목 정렬·영문 소제목(`sections[].align`·`label`), 섹션 배경(`background`).
 * ③-2d가 더한 칸(A·B·C): 첫 화면 배치·덮개·제목 크기·사진 초점(`hero.arrangement`·`image`·`title_size`).
 * 첫 화면 배치·배경·정렬·영문 소제목·줄바꿈·진료 철학 같은 나머지 모양 칸은 다음 PR이다(받아 두기만 한다).
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

/** B안 첫 화면 아래 정보 띠의 칸. `hours`는 지금 띠의 네 번째 칸(토요일·다음 진료 등 그날 문장)이다. */
export type SiteHeroBlock = 'today' | 'phone' | 'location' | 'booking' | 'hours'
const HERO_BLOCKS: ReadonlySet<string> = new Set<SiteHeroBlock>(['today', 'phone', 'location', 'booking', 'hours'])
/** 지금 B안 띠의 칸과 순서(백엔드 `site_defaults`의 `hero.blocks`와 같다). */
export const DEFAULT_HERO_BLOCKS: readonly SiteHeroBlock[] = ['today', 'phone', 'location', 'hours']
export type SiteTreatmentStyle = 'cards' | 'list'
export type SitePhotoRatio = 'portrait' | 'square' | 'landscape'
/** 레이아웃별 지금 의료진 사진 비율 — A 4:3 가로(`.doctorPhoto`), B 4:5·C 3:4 세로. */
export const DEFAULT_PHOTO_RATIO: Readonly<Record<ClinicLayout, SitePhotoRatio>> = { A: 'landscape', B: 'portrait', C: 'portrait' }
/** 바꾼 비율의 CSS 값. 세로는 B의 4:5(지금 세로인 B·C는 세로를 고르면 바꾼 것이 아니다). */
export const PHOTO_ASPECT: Readonly<Record<SitePhotoRatio, string>> = { portrait: '4 / 5', square: '1 / 1', landscape: '4 / 3' }

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
  /** 제목 정렬. 레이아웃 기본과 같으면 null(지금 마크업). */
  align?: SiteSectionAlign | null
  /** 영문 소제목 켜기. 레이아웃 기본과 같으면 null(지금 마크업). */
  label?: boolean | null
  /** 섹션 배경. 레이아웃 기본과 같거나 바꿀 수 없는 섹션이면 null(지금 마크업). */
  background?: SiteSectionBackground | null
}

export type SiteSectionAlign = 'left' | 'center'
export type SiteSectionBackground = 'default' | 'white' | 'tint' | 'band'

/**
 * 레이아웃별 섹션 배경의 지금 모양(백엔드 섹션 표와 같다). 표에 없는 섹션은 'default'(레이아웃 고유 바탕).
 * 지금 진한 띠인 섹션(A 오늘 진료, C 진료 원칙)·다른 섹션에 붙은 섹션(C 최근 소식·진료시간·진료 과정)·머리가 없는
 * 섹션은 배경을 바꾸지 않는다(`BACKGROUND_FIXED`) — 진한 바탕의 흰 글자를 밝은 바탕에 남기지 않게.
 */
export const DEFAULT_SECTION_BACKGROUND: Readonly<Record<ClinicLayout, Partial<Record<SiteSectionId, SiteSectionBackground>>>> = {
  A: { hours: 'band' },
  B: { care_principles: 'tint', care_flow: 'tint' },
  C: { treatments: 'tint', care_principles: 'band', care_flow: 'band' },
}
export const BACKGROUND_FIXED: Readonly<Record<ClinicLayout, ReadonlySet<SiteSectionId>>> = {
  A: new Set<SiteSectionId>(['hero', 'hours']),
  B: new Set<SiteSectionId>(['hero', 'section_index']),
  C: new Set<SiteSectionId>(['hero', 'news', 'hours', 'care_principles', 'care_flow']),
}
const BACKGROUNDS: ReadonlySet<string> = new Set<SiteSectionBackground>(['default', 'white', 'tint', 'band'])

/**
 * 레이아웃별 섹션 제목 정렬·영문 소제목의 지금 모양(백엔드 `site_defaults`의 섹션 표와 같다). 정렬·소제목을
 * 그릴 머리가 없는 섹션(첫 화면·바로가기)은 없다 — 그 섹션의 두 칸은 읽지 않는다.
 */
const CENTER = { align: 'center', label: false } as const
const LEFT = { align: 'left', label: false } as const
const C_HEAD = { align: 'left', label: true } as const
export const DEFAULT_SECTION_LOOK: Readonly<
  Record<ClinicLayout, Partial<Record<SiteSectionId, { align: SiteSectionAlign; label: boolean }>>>
> = {
  A: {
    treatments: CENTER, doctor: CENTER, care_principles: CENTER, hours: CENTER, contents: LEFT, faq: LEFT,
    care_flow: CENTER, gallery: LEFT, facts: LEFT,
  },
  B: {
    treatments: CENTER, doctor: CENTER, care_principles: LEFT, contents: CENTER, questions: LEFT, faq: CENTER,
    care_flow: CENTER, gallery: CENTER, facts: CENTER, visit: CENTER,
  },
  C: {
    news: C_HEAD, hours: C_HEAD, treatments: C_HEAD, doctor: C_HEAD, care_principles: C_HEAD, care_flow: C_HEAD,
    questions: C_HEAD, faq: C_HEAD, gallery: C_HEAD, facts: C_HEAD,
  },
}

/**
 * 영문 소제목 낱말 — C안이 지금 쓰는 낱말 그대로(`HomeLayoutC`), A·B에만 있는 섹션은 같은 말투로 더했다.
 * 제목(h2) 밖의 장식이라 읽지 않는다(aria-hidden).
 */
const COMMON_LABELS: Partial<Record<SiteSectionId, string>> = {
  news: 'NEWS', hours: 'INFORMATION', treatments: 'MEDICAL', doctor: 'DOCTOR', care_principles: 'POLICY',
  care_flow: 'FLOW', questions: 'Q&A', faq: 'FAQ', gallery: 'CLINIC', facts: 'ACCESS', visit: 'LOCATION',
}
export const SECTION_LABEL_WORDS: Readonly<Record<ClinicLayout, Partial<Record<SiteSectionId, string>>>> = {
  A: { ...COMMON_LABELS, contents: 'Q&A' },
  B: { ...COMMON_LABELS, contents: 'ARTICLES' },
  C: { ...COMMON_LABELS },
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
  /**
   * B안만. 기본값(지금 띠)과 같거나 B안이 아니면 null — 컴포넌트는 null이면 지금 마크업을 그대로 그린다.
   */
  hero: { blocks: SiteHeroBlock[] | null; look: HeroLook | null }
  /** B안만. A·C는 언제나 지금 모양(카드·설명 보임)이다 — A 알약·C 타일은 이 칸을 읽지 않는다. */
  treatments: { style: SiteTreatmentStyle; showDescription: boolean }
  /**
   * 의료진 사진·직함(A·B·C). 기본값과 같은 칸은 null·false — 컴포넌트는 지금 마크업을 그대로 그린다.
   * 비율·초점은 사진이 있을 때만 쓴다(사진이 없으면 지금의 이름 첫 글자 타일).
   */
  doctor: { photoRatio: SitePhotoRatio | null; photoFocus: { x: number; y: number } | null; titleBelow: boolean }
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
    const base = DEFAULT_SECTION_LOOK[layout][id]
    sections.push({
      id,
      enabled: FIXED_SECTIONS.has(id) ? true : raw.enabled !== false,
      title: title && title !== DEFAULT_SECTION_TITLES[layout][id] ? title : null,
      align: base && (raw.align === 'left' || raw.align === 'center') && raw.align !== base.align ? raw.align : null,
      label: base && typeof raw.label === 'boolean' && raw.label !== base.label ? raw.label : null,
      background:
        !BACKGROUND_FIXED[layout].has(id) &&
        typeof raw.background === 'string' &&
        BACKGROUNDS.has(raw.background) &&
        raw.background !== (DEFAULT_SECTION_BACKGROUND[layout][id] ?? 'default')
          ? (raw.background as SiteSectionBackground)
          : null,
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
  // 메뉴 'FAQ'는 홈 #faq로 간다 — FAQ 섹션이 꺼졌거나 직접 고른 글이 모두 내려가 FAQ가 비면 뺀다
  // (빈 곳을 가리키지 않게). 자동 선택의 빈 FAQ는 하위 페이지가 글 목록을 모르므로 홈 맨 위로 간다.
  const faqShown =
    sections.some((section) => section.id === 'faq' && section.enabled) &&
    !(mode === 'manual' && (entries ?? []).length === 0)
  const navItems = itemsOf<SiteNavItem>(nav.items, NAV_ITEMS, DEFAULT_NAV_ITEMS).filter(
    (item) => item !== 'faq' || faqShown,
  )

  return {
    layout,
    version: typeof value.version === 'number' ? value.version : null,
    sections,
    copy,
    carePrinciples: { cardCount },
    careFlow: { note: text(flow.note), steps },
    faq: { mode, limit, entries },
    nav: {
      items: navItems,
      mobileBar: itemsOf<SiteMobileBarItem>(nav.mobile_bar, MOBILE_BAR_ITEMS, DEFAULT_MOBILE_BAR),
    },
    share: { imageAssetId: text(share.image_asset_id), description: text(share.description) },
    hero: { blocks: layout === 'B' ? customHeroBlocks(value.hero) : null, look: heroLook(value.hero, layout) },
    treatments: layout === 'B' ? treatmentLook(value.treatments) : { ...DEFAULT_TREATMENT_LOOK },
    doctor: doctorLook(value.doctor, layout),
  }
}

function unit(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null
}

function doctorLook(raw: unknown, layout: ClinicLayout): ClinicSiteConfig['doctor'] {
  const doctor = isRecord(raw) ? raw : {}
  const ratio = doctor.photo_ratio
  const photoRatio =
    (ratio === 'portrait' || ratio === 'square' || ratio === 'landscape') && ratio !== DEFAULT_PHOTO_RATIO[layout]
      ? ratio
      : null
  const focus = isRecord(doctor.photo_focus) ? doctor.photo_focus : {}
  const x = unit(focus.x)
  const y = unit(focus.y)
  // 지금 사진 초점은 세 안 모두 가운데다(설계 14절) — 가운데면 바꾼 것이 아니다.
  const photoFocus = x !== null && y !== null && (x !== 0.5 || y !== 0.5) ? { x, y } : null
  return { photoRatio, photoFocus, titleBelow: doctor.title_position === 'below' }
}

/** 의료진 사진 상자·사진에 덧입힐 모양(바꾼 칸만). 설정이 없으면 null — 지금 마크업 그대로. */
export function physicianPhotoLook(config: ClinicSiteConfig | null): {
  aspectRatio: string | null
  objectPosition: string | null
} | null {
  const doctor = config?.doctor
  if (!doctor || (!doctor.photoRatio && !doctor.photoFocus)) return null
  return {
    aspectRatio: doctor.photoRatio ? PHOTO_ASPECT[doctor.photoRatio] : null,
    objectPosition: doctor.photoFocus
      ? `${Math.round(doctor.photoFocus.x * 100)}% ${Math.round(doctor.photoFocus.y * 100)}%`
      : null,
  }
}

const DEFAULT_TREATMENT_LOOK = { style: 'cards', showDescription: true } as const

/**
 * 첫 화면 모양(③-2d). 기본값과 다른 칸만 싣는다 — 모두 기본이면 null(지금 마크업).
 * - 배치: 지금 세 안 모두 좌우 분할(split). overlay = 사진 위에 글(넓은 화면만 — 좁은 화면은 지금처럼 사진·글을
 *   위아래로 쌓아 글이 사진 위에 놓이지 않는다), text = 글만(사진을 그리지 않는다).
 * - 덮개: 사진 위에 글일 때만. extent text = 글 쪽만, full = 사진 전체. '없음'이면 글 쪽 덮개를 기본 농도(0.6)로 깐다.
 *   색은 레이아웃의 글 바탕색(B 종이색, A 연한 대표색, C 진한 대표색).
 * - 제목 크기: s·l(m = 지금 크기).
 * - 사진 초점: 레이아웃 기본(B 위 48%, A·C 가운데)과 다를 때만.
 */
export interface HeroLook {
  arrangement: 'overlay' | 'text' | null
  titleSize: 's' | 'l' | null
  veil: { extent: 'text' | 'full'; opacity: number } | null
  focus: { x: number; y: number } | null
}

const DEFAULT_HERO_FOCUS: Readonly<Record<ClinicLayout, { x: number; y: number }>> = {
  A: { x: 0.5, y: 0.5 },
  B: { x: 0.5, y: 0.48 },
  C: { x: 0.5, y: 0.5 },
}

function heroLook(raw: unknown, layout: ClinicLayout): HeroLook | null {
  const hero = isRecord(raw) ? raw : {}
  const arrangement = hero.arrangement === 'overlay' || hero.arrangement === 'text' ? hero.arrangement : null
  const titleSize = hero.title_size === 's' || hero.title_size === 'l' ? hero.title_size : null
  const image = isRecord(hero.image) ? hero.image : {}
  const rawVeil = isRecord(image.veil) ? image.veil : {}
  const opacity = unit(rawVeil.opacity)
  const extent: 'text' | 'full' | null = rawVeil.extent === 'text' ? 'text' : rawVeil.extent === 'full' ? 'full' : null
  // 사진 위에 글인데 덮개가 '없음'이면 글 쪽 덮개를 기본 농도로 깐다 — 글이 사진 위에 맨바닥으로 놓이지 않게
  // (편집기의 덮개 기본값이 '없음'이라 배치만 바꾸면 글자가 사진에 묻힌다).
  const veil = arrangement === 'overlay' ? { extent: extent ?? 'text', opacity: extent ? opacity ?? 0.6 : 0.6 } : null
  const rawFocus = isRecord(image.focus) ? image.focus : {}
  const x = unit(rawFocus.x)
  const y = unit(rawFocus.y)
  const base = DEFAULT_HERO_FOCUS[layout]
  const focus = x !== null && y !== null && (x !== base.x || y !== base.y) ? { x, y } : null
  if (!arrangement && !titleSize && !focus) return null
  return { arrangement, titleSize, veil, focus }
}

/** 첫 화면 섹션에 덧붙일 표지·변수(`HeroLook`). null이면 아무것도 붙이지 않는다 — 지금 마크업 그대로. */
export function heroLookAttributes(look: HeroLook | null | undefined): Record<string, unknown> {
  if (!look) return {}
  const style: Record<string, string> = {}
  if (look.titleSize) style['--hero-title-scale'] = look.titleSize === 's' ? '0.85' : '1.15'
  if (look.focus) style['--hero-focus'] = `${Math.round(look.focus.x * 100)}% ${Math.round(look.focus.y * 100)}%`
  if (look.veil) style['--hero-veil-opacity'] = String(look.veil.opacity)
  return {
    'data-hero-arrangement': look.arrangement ?? undefined,
    'data-hero-title': look.titleSize ?? undefined,
    'data-hero-veil': look.veil?.extent ?? undefined,
    'data-hero-focus': look.focus ? '' : undefined,
    style,
  }
}

/** 정보 띠 칸 — 모르는·겹친 칸은 버린다. 남은 칸이 없거나 지금 띠와 같으면 null(지금 마크업). */
function customHeroBlocks(raw: unknown): SiteHeroBlock[] | null {
  const hero = isRecord(raw) ? raw : {}
  if (!Array.isArray(hero.blocks)) return null
  const blocks = itemsOf<SiteHeroBlock>(hero.blocks, HERO_BLOCKS, DEFAULT_HERO_BLOCKS)
  if (blocks.length === 0) return null
  const same =
    blocks.length === DEFAULT_HERO_BLOCKS.length && blocks.every((block, index) => block === DEFAULT_HERO_BLOCKS[index])
  return same ? null : blocks
}

function treatmentLook(raw: unknown): { style: SiteTreatmentStyle; showDescription: boolean } {
  const treatments = isRecord(raw) ? raw : {}
  return {
    style: treatments.style === 'list' ? 'list' : 'cards',
    showDescription: treatments.show_description !== false,
  }
}

/**
 * 정보 띠에 실제로 그릴 칸. 설정이 없으면 null(지금 마크업). '예약'은 네이버 플레이스 주소가 있을 때만 —
 * 없으면 그 칸을 뺀다. 빼고 나니 지금 띠와 같으면 null(지금 띠), 남은 칸이 없으면 빈 배열(띠 없음 —
 * 고르지 않은 기본 칸을 대신 보이지 않는다).
 */
export function resolveHeroBlocks(
  config: ClinicSiteConfig | null,
  bookingUrl: string | null,
): SiteHeroBlock[] | null {
  const blocks = config?.hero.blocks
  if (!blocks) return null
  const shown = bookingUrl ? blocks : blocks.filter((block) => block !== 'booking')
  const same =
    shown.length === DEFAULT_HERO_BLOCKS.length && shown.every((block, index) => block === DEFAULT_HERO_BLOCKS[index])
  return same ? null : shown
}

/**
 * '예약' 칸이 여는 곳 — 네이버 플레이스(병원 정보의 주소, 없으면 플레이스 번호로 만든 주소). 예약 화면으로
 * 바로 가는 주소는 병원마다 달라 만들지 않는다: 플레이스 화면의 예약·문의 버튼으로 이어진다.
 */
export function naverBookingUrl(hospital: {
  naver_place_url?: string | null
  naver_place_id?: string | null
}): string | null {
  const url = hospital.naver_place_url?.trim()
  if (url && /^https?:\/\//.test(url)) return url
  const id = hospital.naver_place_id?.trim()
  return id && /^\d+$/.test(id) ? `https://map.naver.com/p/entry/place/${id}` : null
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
  /** 그 섹션 머리에 보일 영문 소제목(설정 또는 레이아웃 기본). 보이지 않으면 null. */
  label(id: SiteSectionId): string | null
  /** 설정이 바꾼 제목 정렬(기본과 같으면 null — 지금 마크업). */
  align(id: SiteSectionId): SiteSectionAlign | null
  /** 설정이 바꾼 섹션 배경(기본과 같거나 바꿀 수 없는 섹션이면 null — 지금 마크업). */
  background(id: SiteSectionId): SiteSectionBackground | null
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
  const lookLayout: ClinicLayout = layout ?? 'B'
  return {
    order,
    enabled: (id) => order.includes(id),
    label: (id) => {
      const base = DEFAULT_SECTION_LOOK[lookLayout][id]
      if (!base) return null
      const shown = byId.get(id)?.label ?? base.label
      return shown ? SECTION_LABEL_WORDS[lookLayout][id] ?? null : null
    },
    align: (id) => byId.get(id)?.align ?? null,
    background: (id) => byId.get(id)?.background ?? null,
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
    // 직접 고른 글은 개수 설정을 따르지 않는다 — 편집기는 직접 고르기에서 개수를 숨기고, 자동 모드에서
    // 남은 limit(3a 초안의 6 포함)이 고른 글을 자르면 안 된다. 상한은 직접 고르기 상한(10)뿐이다.
    return (config.faq.entries ?? []).slice(0, DEFAULT_FAQ_LIMIT).map((entry) => ({
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
