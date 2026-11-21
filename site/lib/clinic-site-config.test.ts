// 병원 공개 페이지 섹션 설정(레이아웃 편집기) 3b — 공개 응답 `site_config` → 화면 계획.
// 계약: docs/plans/2026-11-20-clinic-site-sections-design.md 1절(I1~I6)·6절.
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import type { ContentSummary } from './api.ts'
import {
  buildHomeSectionPlan,
  DEFAULT_DISABLED_SECTIONS,
  DEFAULT_FAQ_LIMIT,
  DEFAULT_MOBILE_BAR,
  DEFAULT_NAV_ITEMS,
  DEFAULT_SECTION_ORDER,
  DEFAULT_SECTION_TITLES,
  FIXED_SECTIONS,
  DEFAULT_HERO_BLOCKS,
  limitCarePrincipleCards,
  DEFAULT_PHOTO_RATIO,
  BACKGROUND_FIXED,
  DEFAULT_SECTION_BACKGROUND,
  DEFAULT_SECTION_LOOK,
  heroLookAttributes,
  SECTION_LABEL_WORDS,
  naverBookingUrl,
  normalizeSiteConfig,
  physicianPhotoLook,
  resolveCareFlow,
  resolveHeroBlocks,
  resolveHomeFaqEntries,
} from './clinic-site-config.ts'
import { CARE_FLOW_NOTE, CARE_STEPS } from './clinic-template-copy.ts'
import { buildFaqPageJsonLdFromEntries, selectFaqEntries } from './schema.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const SLUG_DIR = join(HERE, '..', 'app', '[slug]')
const DEFAULTS = JSON.parse(readFileSync(join(HERE, 'site-config-defaults.json'), 'utf8')) as {
  defaults_version: string
  schema_version: number
  layouts: Record<'A' | 'B' | 'C', Record<string, any>>
}
const LAYOUTS = ['A', 'B', 'C'] as const
const ROOT = 'https://clinic.example/demo'

/** 공개 응답이 내는 모양 — 완성본 기본값 + 공개 정리(`site_config_public.py`)가 덧붙이는 칸. */
function published(layout: 'A' | 'B' | 'C', patch: (config: Record<string, any>) => void = () => {}) {
  const config = structuredClone(DEFAULTS.layouts[layout])
  config.defaults_version = DEFAULTS.defaults_version
  config.schema_version = DEFAULTS.schema_version
  config.version = 1
  config.faq.entries = null
  patch(config)
  return config
}

function faqContent(id: string, question: string): ContentSummary {
  return {
    id,
    content_type: 'FAQ',
    title: question,
    faq_question: question,
    faq_answer_summary: `${question}에 대한 답입니다.`,
  } as unknown as ContentSummary
}

const CONTENTS = Array.from({ length: 12 }, (_, index) => faqContent(`faq-${index}`, `질문 ${index}번은 무엇인가요?`))

// ── 기본값 = 지금 화면 ────────────────────────────────────────────────

test('the site defaults equal the backend defaults fixture (order, titles, menus, FAQ limit)', () => {
  for (const layout of LAYOUTS) {
    const sections = DEFAULTS.layouts[layout].sections as Array<{ id: string; enabled: boolean; title: string | null }>
    assert.deepEqual([...DEFAULT_SECTION_ORDER[layout]], sections.map((section) => section.id), layout)
    for (const section of sections) {
      assert.equal(DEFAULT_SECTION_TITLES[layout][section.id as never] ?? null, section.title, `${layout} ${section.id}`)
      assert.equal(!DEFAULT_DISABLED_SECTIONS.has(section.id as never), section.enabled, `${layout} ${section.id}`)
    }
    assert.deepEqual([...DEFAULT_NAV_ITEMS], DEFAULTS.layouts[layout].nav.items)
    assert.deepEqual([...DEFAULT_MOBILE_BAR], DEFAULTS.layouts[layout].nav.mobile_bar)
    assert.equal(DEFAULT_FAQ_LIMIT, DEFAULTS.layouts[layout].faq.limit)
  }
  assert.deepEqual([...FIXED_SECTIONS].sort(), ['doctor', 'facts', 'hero', 'hours'])
})

test('the FAQ default limit is the current home FAQ limit', () => {
  assert.equal(selectFaqEntries(CONTENTS, ROOT).length, DEFAULT_FAQ_LIMIT)
})

for (const layout of LAYOUTS) {
  test(`a published default config for layout ${layout} plans the same screen as no config`, () => {
    const config = normalizeSiteConfig(published(layout), layout)
    assert.ok(config)
    const withConfig = buildHomeSectionPlan(layout, config)
    const without = buildHomeSectionPlan(layout, null)
    assert.deepEqual(withConfig.order, without.order)
    for (const id of DEFAULT_SECTION_ORDER[layout]) {
      // 기본 제목은 '바꾼 제목'이 아니다 — 컴포넌트가 지금 제목(병원 정보 제목 등)을 그대로 쓴다.
      assert.equal(withConfig.customTitle(id), null, id)
      assert.equal(withConfig.customNote(id), null, id)
    }
    assert.deepEqual(
      resolveHomeFaqEntries(CONTENTS, ROOT, config, withConfig),
      resolveHomeFaqEntries(CONTENTS, ROOT, null, without),
    )
    assert.deepEqual(resolveCareFlow(config), resolveCareFlow(null))
    assert.deepEqual(config.nav.items, [...DEFAULT_NAV_ITEMS])
    assert.deepEqual(config.nav.mobileBar, [...DEFAULT_MOBILE_BAR])
    assert.deepEqual(limitCarePrincipleCards([1, 2, 3], config), [1, 2, 3])
    assert.deepEqual(limitCarePrincipleCards([1, 2, 3], config, [null, null, null]), [1, 2, 3])
    assert.equal(config.visit, null)
    // 모양 칸(③-2a)도 기본값이면 지금 마크업이다 — 정보 띠 칸 null, 진료 항목 카드·설명 보임.
    assert.equal(config.hero.blocks, null)
    assert.equal(config.hero.look, null)
    assert.deepEqual(heroLookAttributes(config.hero.look), {})
    assert.equal(resolveHeroBlocks(config, 'https://map.naver.com/p/entry/place/1'), null)
    assert.deepEqual(config.treatments, { style: 'cards', showDescription: true })
    // ③-2b 의료진 칸도 기본값이면 지금 마크업이다.
    assert.deepEqual(config.doctor, { photoRatio: null, photoFocus: null, titleBelow: false })
    // ③-2c 섹션 머리도 기본값이면 지금 마크업이다 — 정렬 틀 없음, 소제목은 레이아웃 기본(C만 보임).
    for (const id of DEFAULT_SECTION_ORDER[layout]) {
      assert.equal(withConfig.align(id), null, `${layout} ${id}`)
      assert.equal(withConfig.background(id), null, `${layout} ${id}`)
      assert.equal(withConfig.label(id), without.label(id), `${layout} ${id}`)
    }
    assert.equal(physicianPhotoLook(config), null)
  })
}

test('the default doctor photo ratio per layout equals the backend default', () => {
  for (const layout of LAYOUTS) assert.equal(DEFAULT_PHOTO_RATIO[layout], DEFAULTS.layouts[layout].doctor.photo_ratio, layout)
})

test('the default info strip blocks equal the backend default', () => {
  for (const layout of LAYOUTS) assert.deepEqual([...DEFAULT_HERO_BLOCKS], DEFAULTS.layouts[layout].hero.blocks)
})

test('no config plans the current markup order (the hospitals without a layout use B)', () => {
  assert.deepEqual(buildHomeSectionPlan(null, null).order, DEFAULT_SECTION_ORDER.B.filter((id) => id !== 'section_index'))
  assert.equal(buildHomeSectionPlan(null, null).enabled('section_index'), false)
})

// ── 받은 값을 좁혀 쓴다 ───────────────────────────────────────────────

test('missing, malformed, other-version or other-layout configs fall back to the current screen', () => {
  assert.equal(normalizeSiteConfig(null, 'A'), null)
  assert.equal(normalizeSiteConfig('x', 'A'), null)
  assert.equal(normalizeSiteConfig(published('A', (c) => (c.schema_version = 2)), 'A'), null)
  assert.equal(normalizeSiteConfig(published('A'), 'C'), null)
  assert.equal(normalizeSiteConfig(published('B'), null), null)
  assert.equal(normalizeSiteConfig(published('A', (c) => (c.sections = 'x')), 'A'), null)
  assert.equal(normalizeSiteConfig(published('A', (c) => c.sections.push({ id: 'unknown' })), 'A'), null)
})

test('fixed sections stay on even if a config turns them off; the hero stays first', () => {
  const config = normalizeSiteConfig(
    published('A', (c) => {
      for (const section of c.sections) if (['doctor', 'facts', 'hours', 'hero'].includes(section.id)) section.enabled = false
      c.sections.push(c.sections.shift())
    }),
    'A',
  )
  const plan = buildHomeSectionPlan('A', config)
  assert.equal(plan.order[0], 'hero')
  for (const id of ['doctor', 'facts', 'hours'] as const) assert.ok(plan.enabled(id), id)
})

test('a turned-off section leaves the order and its title/note overrides apply to the rest', () => {
  const config = normalizeSiteConfig(
    published('B', (c) => {
      c.sections.find((s: any) => s.id === 'gallery').enabled = false
      c.sections.find((s: any) => s.id === 'treatments').title = '진료 과목'
      c.copy = { 'treatments.note': '자주 찾는 진료입니다.' }
      // 순서 바꾸기: 질문 목록과 FAQ 자리 맞바꿈.
      const q = c.sections.findIndex((s: any) => s.id === 'questions')
      const f = c.sections.findIndex((s: any) => s.id === 'faq')
      ;[c.sections[q], c.sections[f]] = [c.sections[f], c.sections[q]]
    }),
    'B',
  )
  const plan = buildHomeSectionPlan('B', config)
  assert.ok(!plan.order.includes('gallery'))
  assert.ok(plan.order.indexOf('faq') < plan.order.indexOf('questions'))
  assert.equal(plan.title('treatments', '진료 영역'), '진료 과목')
  assert.equal(plan.note('treatments', '기본 안내'), '자주 찾는 진료입니다.')
  assert.equal(plan.note('faq', '기본 안내'), '기본 안내')
})

test('A keeps questions and FAQ in one #contents wrapper only while they stay adjacent', () => {
  assert.ok(buildHomeSectionPlan('A', null).adjacent('contents', 'faq'))
  const apart = normalizeSiteConfig(
    published('A', (c) => {
      const faq = c.sections.findIndex((s: any) => s.id === 'faq')
      const flow = c.sections.findIndex((s: any) => s.id === 'care_flow')
      ;[c.sections[faq], c.sections[flow]] = [c.sections[flow], c.sections[faq]]
    }),
    'A',
  )
  assert.equal(buildHomeSectionPlan('A', apart).adjacent('contents', 'faq'), false)
})

// ── FAQ: 화면 = FAQPage (I2) ──────────────────────────────────────────

test('turning the FAQ section off empties the FAQ (no FAQPage either)', () => {
  const config = normalizeSiteConfig(published('C', (c) => (c.sections.find((s: any) => s.id === 'faq').enabled = false)), 'C')
  assert.deepEqual(resolveHomeFaqEntries(CONTENTS, ROOT, config, buildHomeSectionPlan('C', config)), [])
})

test('auto FAQ keeps the current selection rule with the configured limit', () => {
  const config = normalizeSiteConfig(published('B', (c) => (c.faq.limit = 4)), 'B')
  const entries = resolveHomeFaqEntries(CONTENTS, ROOT, config, buildHomeSectionPlan('B', config))
  assert.deepEqual(entries, selectFaqEntries(CONTENTS, ROOT, 4))
})

test('manual FAQ uses the server-read approved entries in the chosen order, never other posts', () => {
  const config = normalizeSiteConfig(
    published('B', (c) => {
      c.faq.mode = 'manual'
      c.faq.entries = [
        { id: 'faq-9', question: '질문 9번은 무엇인가요?', answer: '답 9' },
        { id: 'faq-2', question: '질문 2번은 무엇인가요?', answer: '답 2' },
        { id: 'faq-x', question: '', answer: '빈 질문은 버린다' },
      ]
    }),
    'B',
  )
  const entries = resolveHomeFaqEntries(CONTENTS, ROOT, config, buildHomeSectionPlan('B', config))
  assert.deepEqual(
    entries.map((entry) => [entry.id, entry.url]),
    [
      ['faq-9', `${ROOT}/contents/faq-9`],
      ['faq-2', `${ROOT}/contents/faq-2`],
    ],
  )
  // 자동 모드에서 남은 개수(3)는 직접 고른 글을 자르지 않는다 — 고른 다섯 개가 화면·FAQPage에 모두 실린다.
  const five = Array.from({ length: 5 }, (_, i) => ({ id: `m-${i}`, question: `고른 질문 ${i}번은 무엇인가요?`, answer: `답 ${i}` }))
  const leftover = normalizeSiteConfig(
    published('B', (c) => ((c.faq.mode = 'manual'), (c.faq.limit = 3), (c.faq.entries = five))),
    'B',
  )
  const chosen = resolveHomeFaqEntries(CONTENTS, ROOT, leftover, buildHomeSectionPlan('B', leftover))
  assert.deepEqual(chosen.map((entry) => entry.id), five.map((entry) => entry.id))
  assert.equal((buildFaqPageJsonLdFromEntries(chosen, `${ROOT}/`)?.mainEntity as unknown[] | undefined)?.length, 5)
  const allDown = normalizeSiteConfig(published('B', (c) => ((c.faq.mode = 'manual'), (c.faq.entries = []))), 'B')
  assert.deepEqual(resolveHomeFaqEntries(CONTENTS, ROOT, allDown, buildHomeSectionPlan('B', allDown)), [])
})

// ── 진료 과정·원칙 카드 ────────────────────────────────────────────────

test('care flow keeps the default steps (with their aux line) and shows edited steps plainly', () => {
  assert.deepEqual(resolveCareFlow(null), { note: CARE_FLOW_NOTE, steps: CARE_STEPS.map((step) => ({ ...step })) })
  const config = normalizeSiteConfig(
    published('B', (c) => {
      c.care_flow.note = '예약 없이 오셔도 됩니다.'
      c.care_flow.steps = [c.care_flow.steps[0], { title: '검사', body: '필요한 검사를 안내합니다.' }]
    }),
    'B',
  )
  const flow = resolveCareFlow(config)
  assert.equal(flow.note, '예약 없이 오셔도 됩니다.')
  assert.deepEqual(flow.steps[0], { ...CARE_STEPS[0] })
  assert.deepEqual(flow.steps[1], { label: '02', aux: null, title: '검사', body: '필요한 검사를 안내합니다.' })
})

test('care principle cards are cut to the configured count', () => {
  const config = normalizeSiteConfig(published('A', (c) => (c.care_principles.card_count = 2)), 'A')
  assert.deepEqual(limitCarePrincipleCards(['a', 'b', 'c'], config), ['a', 'b'])
})

// ── 메뉴 ──────────────────────────────────────────────────────────────

test('menu and mobile bar follow the config order and drop unknown or repeated items', () => {
  const config = normalizeSiteConfig(
    published('B', (c) => {
      c.nav.items = ['doctor', 'faq', 'doctor', 'nope', 'call']
      c.nav.mobile_bar = ['map', 'call']
    }),
    'B',
  )
  assert.deepEqual(config?.nav.items, ['doctor', 'faq', 'call'])
  assert.deepEqual(config?.nav.mobileBar, ['map', 'call'])
})

test('the FAQ menu item disappears when the home FAQ is off or every chosen FAQ is down', () => {
  const off = normalizeSiteConfig(
    published('B', (c) => {
      c.nav.items = ['faq', 'call']
      c.sections.find((s: any) => s.id === 'faq').enabled = false
    }),
    'B',
  )
  assert.deepEqual(off?.nav.items, ['call'])
  const empty = normalizeSiteConfig(
    published('B', (c) => ((c.nav.items = ['faq', 'call']), (c.faq.mode = 'manual'), (c.faq.entries = []))),
    'B',
  )
  assert.deepEqual(empty?.nav.items, ['call'])
  const shown = normalizeSiteConfig(published('B', (c) => (c.nav.items = ['faq', 'call'])), 'B')
  assert.deepEqual(shown?.nav.items, ['faq', 'call'])
})

test('C draws the care flow on its own band when the principles are off', () => {
  const source = readFileSync(join(SLUG_DIR, '_layouts', 'HomeLayoutC.tsx'), 'utf8')
  // 진료 원칙이 꺼졌거나 보일 카드가 0장이면(기본 카드 끄기 ③-2e) 진료 과정만 자기 띠에 그린다.
  assert.match(source, /const principlesShown = plan\.enabled\('care_principles'\) && principleCards\.length > 0/)
  assert.match(source, /care_flow:\s*!principlesShown && flowBlock \?/)
})

test('step labels keep the same text nodes as before (label, " · ", aux)', () => {
  for (const name of ['HomeLayoutA.tsx', 'HomeLayoutC.tsx']) {
    const source = readFileSync(join(SLUG_DIR, '_layouts', name), 'utf8')
    assert.match(source, /\{step\.label\}\{step\.aux \? <> · \{step\.aux\}<\/> : null\}/, name)
  }
})

// ── 구조화 정보·llms.txt는 설정을 읽지 않는다 (I1) ─────────────────────

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? sourceFiles(path) : /\.(ts|tsx)$/.test(name) ? [path] : []
  })
}

test('the clinic JSON-LD and llms.txt never read the section config', () => {
  const page = readFileSync(join(SLUG_DIR, '_layouts', 'HospitalHome.tsx'), 'utf8')
  const clinicJsonLd = page.slice(page.indexOf('const clinicJsonLd = {'), page.indexOf('const breadcrumbJsonLd'))
  assert.ok(clinicJsonLd.includes("'@type': ['MedicalClinic', 'LocalBusiness']"))
  assert.doesNotMatch(clinicJsonLd, /site_?[cC]onfig|plan\./)
  // 설정은 JSON-LD를 만든 뒤에 읽는다.
  assert.ok(page.indexOf('const siteConfig = hospital.site_config') > page.indexOf('const clinicJsonLd = {'))
  for (const file of sourceFiles(join(SLUG_DIR, 'llms.txt'))) {
    assert.doesNotMatch(readFileSync(file, 'utf8'), /site_?[cC]onfig/, file)
  }
  for (const name of ['clinic-schema.ts', 'llms-text.ts']) {
    try {
      assert.doesNotMatch(readFileSync(join(HERE, name), 'utf8'), /site_?[cC]onfig/, name)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
    }
  }
})

test('every hospital page passes the same menu config to the header', () => {
  const pages = sourceFiles(SLUG_DIR).filter((file) => readFileSync(file, 'utf8').includes('<ClinicHeader'))
  assert.ok(pages.length >= 7)
  for (const file of pages) {
    const source = readFileSync(file, 'utf8')
    const headers = source.split('<ClinicHeader').slice(1).map((part) => part.slice(0, part.indexOf('/>')))
    for (const header of headers) assert.match(header, /siteConfig=\{\w+\.site_config\}/, file)
  }
})

// ── ③-2a 모양 칸: B안 정보 띠 칸·진료 항목 모양 ─────────────────────────

const PLACE = 'https://m.place.naver.com/place/1595672233'

test('B reads the chosen info strip blocks, dropping unknown or repeated ones', () => {
  const config = normalizeSiteConfig(
    published('B', (c) => (c.hero.blocks = ['phone', 'location', 'phone', 'weather', 'booking'])),
    'B',
  )
  assert.deepEqual(config?.hero.blocks, ['phone', 'location', 'booking'])
  assert.deepEqual(resolveHeroBlocks(config, PLACE), ['phone', 'location', 'booking'])
})

test('blocks equal to the current strip, empty or malformed stay on the current markup', () => {
  assert.equal(normalizeSiteConfig(published('B', (c) => (c.hero.blocks = [...DEFAULT_HERO_BLOCKS])), 'B')?.hero.blocks, null)
  assert.equal(normalizeSiteConfig(published('B', (c) => (c.hero.blocks = ['weather'])), 'B')?.hero.blocks, null)
  assert.equal(normalizeSiteConfig(published('B', (c) => (c.hero.blocks = 'phone')), 'B')?.hero.blocks, null)
  assert.equal(normalizeSiteConfig(published('B', (c) => (c.hero = null)), 'B')?.hero.blocks, null)
})

test('the booking block needs a Naver place; without it the block is dropped', () => {
  const withBooking = normalizeSiteConfig(
    published('B', (c) => (c.hero.blocks = ['today', 'phone', 'booking', 'location', 'hours'])),
    'B',
  )
  assert.deepEqual(resolveHeroBlocks(withBooking, PLACE), ['today', 'phone', 'booking', 'location', 'hours'])
  // 빼고 나니 지금 띠와 같다 → 지금 마크업.
  assert.equal(resolveHeroBlocks(withBooking, null), null)
  const onlyBooking = normalizeSiteConfig(published('B', (c) => (c.hero.blocks = ['booking'])), 'B')
  // 고른 칸이 모두 빠지면 띠가 없다 — 고르지 않은 기본 칸을 대신 보이지 않는다.
  assert.deepEqual(resolveHeroBlocks(onlyBooking, null), [])
  assert.deepEqual(resolveHeroBlocks(onlyBooking, PLACE), ['booking'])
  assert.equal(resolveHeroBlocks(null, PLACE), null)
})

test('B reads the treatment look; A and C always keep the current look', () => {
  const list = (layout: 'A' | 'B' | 'C') =>
    normalizeSiteConfig(
      published(layout, (c) => {
        c.treatments = { style: 'list', show_description: false }
        c.hero.blocks = ['phone', 'location']
      }),
      layout,
    )
  assert.deepEqual(list('B')?.treatments, { style: 'list', showDescription: false })
  for (const layout of ['A', 'C'] as const) {
    assert.deepEqual(list(layout)?.treatments, { style: 'cards', showDescription: true }, layout)
    assert.equal(list(layout)?.hero.blocks, null, layout)
  }
  assert.deepEqual(
    normalizeSiteConfig(published('B', (c) => (c.treatments = { style: 'grid' })), 'B')?.treatments,
    { style: 'cards', showDescription: true },
  )
})

test('the booking link is the Naver place page (stored URL first, else built from the place id)', () => {
  assert.equal(naverBookingUrl({ naver_place_url: ` ${PLACE} `, naver_place_id: '9' }), PLACE)
  assert.equal(naverBookingUrl({ naver_place_url: null, naver_place_id: '1595672233' }), 'https://map.naver.com/p/entry/place/1595672233')
  assert.equal(naverBookingUrl({ naver_place_url: 'javascript:alert(1)', naver_place_id: null }), null)
  assert.equal(naverBookingUrl({ naver_place_url: null, naver_place_id: 'abc' }), null)
  assert.equal(naverBookingUrl({}), null)
})

// ── ③-2b 의료진 사진 비율·초점, 직함 위치(A·B·C) ─────────────────────────

test('a doctor photo ratio counts only when it differs from the layout default', () => {
  const ratio = (layout: 'A' | 'B' | 'C', value: string) =>
    normalizeSiteConfig(published(layout, (c) => (c.doctor.photo_ratio = value)), layout)?.doctor.photoRatio
  assert.equal(ratio('A', 'landscape'), null)
  assert.equal(ratio('A', 'portrait'), 'portrait')
  assert.equal(ratio('B', 'portrait'), null)
  assert.equal(ratio('B', 'square'), 'square')
  assert.equal(ratio('C', 'portrait'), null)
  assert.equal(ratio('C', 'landscape'), 'landscape')
  assert.equal(ratio('B', 'wide'), null)
})

test('the photo look carries only the changed ratio and focus', () => {
  const look = (patch: (c: Record<string, any>) => void) =>
    physicianPhotoLook(normalizeSiteConfig(published('B', patch), 'B'))
  assert.deepEqual(look((c) => (c.doctor.photo_ratio = 'square')), { aspectRatio: '1 / 1', objectPosition: null })
  assert.deepEqual(look((c) => (c.doctor.photo_focus = { x: 0.5, y: 0.2 })), { aspectRatio: null, objectPosition: '50% 20%' })
  assert.equal(look((c) => (c.doctor.photo_focus = { x: 0.5, y: 0.5 })), null)
  assert.equal(look((c) => (c.doctor.photo_focus = { x: 2, y: 0.2 })), null)
  assert.equal(look((c) => (c.doctor.photo_focus = 'top')), null)
  assert.equal(physicianPhotoLook(null), null)
})

test('the title goes below the name only when the config says so', () => {
  for (const layout of LAYOUTS) {
    const below = normalizeSiteConfig(published(layout, (c) => (c.doctor.title_position = 'below')), layout)
    assert.equal(below?.doctor.titleBelow, true, layout)
    const above = normalizeSiteConfig(published(layout, (c) => (c.doctor.title_position = 'side')), layout)
    assert.equal(above?.doctor.titleBelow, false, layout)
  }
})

// ── ③-2c 섹션 머리: 제목 정렬·영문 소제목 ─────────────────────────────

test('the section head defaults equal the backend defaults (align, label)', () => {
  for (const layout of LAYOUTS) {
    for (const section of DEFAULTS.layouts[layout].sections as Array<{ id: string; align: string; label: boolean }>) {
      const look = DEFAULT_SECTION_LOOK[layout][section.id as never] as { align: string; label: boolean } | undefined
      if (section.id === 'hero' || section.id === 'section_index') {
        assert.equal(look, undefined, `${layout} ${section.id} has no head`)
        continue
      }
      assert.deepEqual(look, { align: section.align, label: section.label }, `${layout} ${section.id}`)
    }
  }
})

test('C keeps its current English labels; A and B show none by default', () => {
  const c = buildHomeSectionPlan('C', null)
  const expected = {
    news: 'NEWS', hours: 'INFORMATION', treatments: 'MEDICAL', doctor: 'DOCTOR', care_principles: 'POLICY',
    care_flow: 'FLOW', questions: 'Q&A', faq: 'FAQ', gallery: 'CLINIC', facts: 'ACCESS',
  } as const
  for (const [id, word] of Object.entries(expected)) assert.equal(c.label(id as never), word, id)
  for (const layout of ['A', 'B'] as const) {
    const plan = buildHomeSectionPlan(layout, null)
    for (const id of DEFAULT_SECTION_ORDER[layout]) assert.equal(plan.label(id), null, `${layout} ${id}`)
  }
  for (const layout of LAYOUTS) {
    for (const id of Object.keys(DEFAULT_SECTION_LOOK[layout])) {
      assert.ok(SECTION_LABEL_WORDS[layout][id as never], `${layout} ${id} needs a label word`)
    }
  }
})

test('a changed align or label shows; the same as the default does not; heads-less sections ignore them', () => {
  const plan = (layout: 'A' | 'B' | 'C', patch: (sections: Array<Record<string, any>>) => void) => {
    const config = normalizeSiteConfig(published(layout, (c) => patch(c.sections)), layout)
    return buildHomeSectionPlan(layout, config)
  }
  const set = (id: string, values: Record<string, unknown>) => (sections: Array<Record<string, any>>) =>
    Object.assign(sections.find((section) => section.id === id) ?? {}, values)
  const b = plan('B', set('treatments', { align: 'left', label: true }))
  assert.equal(b.align('treatments'), 'left')
  assert.equal(b.label('treatments'), 'MEDICAL')
  assert.equal(plan('B', set('treatments', { align: 'center', label: false })).align('treatments'), null)
  const c = plan('C', set('faq', { label: false, align: 'center' }))
  assert.equal(c.label('faq'), null)
  assert.equal(c.align('faq'), 'center')
  const hero = plan('B', set('hero', { align: 'center', label: true }))
  assert.equal(hero.align('hero'), null)
  assert.equal(hero.label('hero'), null)
})

// ── ③-2c 섹션 배경 ──────────────────────────────────────────────────

test('the section background defaults equal the backend defaults', () => {
  for (const layout of LAYOUTS) {
    for (const section of DEFAULTS.layouts[layout].sections as Array<{ id: string; background: string }>) {
      assert.equal(DEFAULT_SECTION_BACKGROUND[layout][section.id as never] ?? 'default', section.background, `${layout} ${section.id}`)
    }
  }
})

test('a changed background shows; dark-band, attached and head-less sections keep theirs', () => {
  const plan = (layout: 'A' | 'B' | 'C', id: string, background: string) =>
    buildHomeSectionPlan(
      layout,
      normalizeSiteConfig(
        published(layout, (c) => Object.assign(c.sections.find((x: { id: string }) => x.id === id) ?? {}, { background })),
        layout,
      ),
    ).background(id as never)
  assert.equal(plan('B', 'treatments', 'band'), 'band')
  assert.equal(plan('B', 'care_principles', 'default'), 'default')
  assert.equal(plan('B', 'care_principles', 'tint'), null)
  assert.equal(plan('B', 'faq', 'stripes'), null)
  assert.equal(plan('A', 'hours', 'white'), null)
  assert.equal(plan('C', 'care_principles', 'white'), null)
  assert.equal(plan('C', 'news', 'band'), null)
  assert.equal(plan('C', 'treatments', 'default'), 'default')
  for (const layout of LAYOUTS) assert.ok(BACKGROUND_FIXED[layout].has('hero'), layout)
})

// ── ③-2d 첫 화면 배치·덮개·제목 크기·사진 초점 ─────────────────────────

test('the hero look carries only changed values and a safe veil for text over the photo', () => {
  const look = (layout: 'A' | 'B' | 'C', patch: (hero: Record<string, any>) => void) =>
    normalizeSiteConfig(published(layout, (c) => patch(c.hero)), layout)?.hero.look ?? null
  assert.equal(look('B', (h) => (h.image.focus = { x: 0.5, y: 0.48 })), null)
  assert.deepEqual(look('A', (h) => (h.image.focus = { x: 0.5, y: 0.48 }))?.focus, { x: 0.5, y: 0.48 })
  // 사진 위에 글 + 덮개 '없음'(편집기 기본) → 글 쪽 덮개를 기본 농도로.
  assert.deepEqual(look('B', (h) => (h.arrangement = 'overlay'))?.veil, { extent: 'text', opacity: 0.6 })
  assert.deepEqual(
    look('C', (h) => {
      h.arrangement = 'overlay'
      h.image.veil = { extent: 'full', opacity: 0.8 }
    })?.veil,
    { extent: 'full', opacity: 0.8 },
  )
  // 덮개는 사진 위에 글일 때만.
  assert.equal(look('B', (h) => {
    h.arrangement = 'text'
    h.image.veil = { extent: 'full', opacity: 0.8 }
  })?.veil, null)
  assert.equal(look('B', (h) => (h.title_size = 'm')), null)
  assert.equal(look('B', (h) => (h.arrangement = 'split')), null)
})

test('the hero look becomes data attributes and CSS variables', () => {
  const attrs = heroLookAttributes({ arrangement: 'overlay', titleSize: 'l', veil: { extent: 'text', opacity: 0.7 }, focus: { x: 0.5, y: 0.2 } })
  assert.equal(attrs['data-hero-arrangement'], 'overlay')
  assert.equal(attrs['data-hero-title'], 'l')
  assert.equal(attrs['data-hero-veil'], 'text')
  assert.equal(attrs['data-hero-focus'], '')
  assert.deepEqual(attrs.style, { '--hero-title-scale': '1.15', '--hero-focus': '50% 20%', '--hero-veil-opacity': '0.7' })
})

// ── ③-2e 진료 원칙 기본 카드·오시는 길 ───────────────────────────────

test('turning default cards off keeps only the cards the hospital wrote, then the count', () => {
  const config = normalizeSiteConfig(published('A', (c) => (c.care_principles.use_default_cards = false)), 'A')
  assert.deepEqual(limitCarePrincipleCards(['a', 'b', 'c'], config, [null, { title: 't', body: 'b' }, null]), ['b'])
  assert.deepEqual(limitCarePrincipleCards(['a', 'b', 'c'], config, null), [])
  // 병원 카드 정보를 넘기지 않는 호출은 지금 동작(기본 카드 포함)이다.
  assert.deepEqual(limitCarePrincipleCards(['a', 'b', 'c'], config), ['a', 'b', 'c'])
  const counted = normalizeSiteConfig(
    published('A', (c) => {
      c.care_principles.use_default_cards = false
      c.care_principles.card_count = 1
    }),
    'A',
  )
  assert.deepEqual(limitCarePrincipleCards(['a', 'b', 'c'], counted, [{}, {}, null]), ['a'])
})

test('the visit look reads the order and the transit switch; the current order is null', () => {
  const visit = (patch: (v: Record<string, any>) => void) => normalizeSiteConfig(published('B', (c) => patch(c.visit)), 'B')?.visit
  assert.equal(visit(() => {}), null)
  assert.deepEqual(visit((v) => (v.order = ['transit', 'parking'])), { order: ['transit', 'parking'], showTransit: true })
  assert.deepEqual(visit((v) => (v.show_transit = false)), { order: ['parking', 'transit'], showTransit: false })
  assert.equal(visit((v) => (v.order = ['parking'])), null)
})
