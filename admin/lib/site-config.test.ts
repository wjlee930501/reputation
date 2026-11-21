import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  FIXED_SECTIONS,
  UNTITLED_SECTIONS,
  BACKGROUND_FIXED,
  B_ONLY_FIELDS,
  B_ONLY_LINE_BREAK_PATHS,
  UNRENDERED_COPY_KEYS,
  shownCopyKeys,
  lineBreakPathShown,
  UNRENDERED_FIELDS,
  sectionLookShown,
  buildOverrides,
  canDisable,
  canMoveDown,
  canMoveUp,
  canRetitle,
  discardDraftPath,
  draftExpectation,
  issuesForSection,
  moveSection,
  overridesCompareKey,
  readSiteConfigFailure,
  readSiteConfigState,
  type ResolvedSiteConfig,
  type SiteSection,
  showsBOnlyFields,
} from './site-config.ts'

function section(id: string, extra: Partial<SiteSection> = {}): SiteSection {
  return {
    id: id as SiteSection['id'],
    enabled: true,
    title: null,
    background: 'default',
    align: 'left',
    label: false,
    attached_to: null,
    ...extra,
  }
}

// C안 기본값 일부 — 붙은 섹션(news·hours는 첫 화면, care_flow는 원칙)을 담는다.
function defaultsC(): ResolvedSiteConfig {
  return {
    layout: 'C',
    sections: [
      section('hero'),
      section('news', { title: '최근 의료 정보', attached_to: 'hero' }),
      section('hours', { title: '진료시간', attached_to: 'hero' }),
      section('treatments', { title: '진료 영역', background: 'tint', label: true }),
      section('doctor', { title: '의료진 소개', label: true }),
      section('care_principles', { title: '증상의 원인부터 확인합니다', background: 'band' }),
      section('care_flow', { title: '진료 4단계', background: 'band', attached_to: 'care_principles' }),
      section('faq', { title: '자주 묻는 질문과 답변 요약', label: true }),
      section('facts'),
    ],
    hero: {
      arrangement: 'split',
      title_size: 'm',
      line_breaks: {},
      blocks: ['today', 'phone', 'location', 'hours'],
      image: { focus: { x: 0.5, y: 0.5 }, veil: { opacity: 0.6, extent: 'text' } },
    },
    copy: {},
    faq: { mode: 'auto', limit: 6, content_ids: [], merge_questions: false },
    share: { image_asset_id: null, description: null },
    defaults_version: '2026-11-21.1',
    schema_version: 1,
  }
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

test('section rules mirror backend site_defaults', () => {
  const source = readFileSync(new URL('../../backend/app/services/site_defaults.py', import.meta.url), 'utf8')
  const set = (name: string) =>
    [...(source.match(new RegExp(`${name}: Final = frozenset\\(\\{([^}]*)\\}`))?.[1] ?? '').matchAll(/"([a-z_]+)"/g)]
      .map((m) => m[1])
      .sort()
  assert.deepEqual(set('FIXED_SECTIONS'), [...FIXED_SECTIONS].sort())
  assert.deepEqual(set('UNTITLED_SECTIONS'), [...UNTITLED_SECTIONS].sort())
  assert.match(source, /FIRST_SECTION: Final = "hero"/)
})

test('unchanged defaults save as an empty override', () => {
  assert.deepEqual(buildOverrides(defaultsC(), defaultsC()), {})
})

test('overrides carry only changed keys and never resolved-only fields', () => {
  const edited = clone(defaultsC())
  edited.sections[3].title = '진료 과목'
  edited.sections[7].enabled = false
  ;(edited.hero as { image: { focus: { y: number } } }).image.focus.y = 0.3
  ;(edited.faq as { limit: number }).limit = 4
  const overrides = buildOverrides(edited, defaultsC())
  assert.deepEqual(overrides, {
    sections: [
      { id: 'treatments', title: '진료 과목' },
      { id: 'faq', enabled: false },
    ],
    hero: { image: { focus: { x: 0.5, y: 0.3 }, veil: { opacity: 0.6, extent: 'text' } } },
    faq: { limit: 4 },
  })
  const text = JSON.stringify(overrides)
  for (const forbidden of ['attached_to', 'defaults_version', 'schema_version', '"layout"']) {
    assert.ok(!text.includes(forbidden), forbidden)
  }
})

test('copy and line breaks replace wholesale; null share fields stay out', () => {
  const edited = clone(defaultsC())
  edited.copy = { 'faq.note': '자주 묻는 질문입니다' }
  ;(edited.hero as Record<string, unknown>).line_breaks = { 'hero.headline': { pc: '가\n나', mobile: null } }
  assert.deepEqual(buildOverrides(edited, defaultsC()), {
    copy: { 'faq.note': '자주 묻는 질문입니다' },
    hero: { line_breaks: { 'hero.headline': { pc: '가\n나', mobile: null } } },
  })
})

test('a reorder lists every section so the server keeps the new order', () => {
  const moved = moveSection(defaultsC().sections, 'faq', -1)
  const edited = { ...defaultsC(), sections: moved }
  const overrides = buildOverrides(edited, defaultsC())
  assert.deepEqual(
    (overrides.sections as { id: string }[]).map((s) => s.id),
    ['hero', 'news', 'hours', 'treatments', 'doctor', 'faq', 'care_principles', 'care_flow', 'facts'],
  )
})

test('attached sections move with their host and the hero stays first', () => {
  const sections = defaultsC().sections
  // 원칙을 위로 — 붙은 진료 과정이 함께 올라간다.
  assert.deepEqual(
    moveSection(sections, 'care_principles', -1).map((s) => s.id),
    ['hero', 'news', 'hours', 'treatments', 'care_principles', 'care_flow', 'doctor', 'faq', 'facts'],
  )
  // 붙은 섹션·첫 화면은 따로 움직이지 않는다.
  assert.equal(canMoveUp(sections, 'care_flow'), false)
  assert.equal(canMoveDown(sections, 'news'), false)
  assert.equal(canMoveDown(sections, 'hero'), false)
  // 첫 화면 덩어리 바로 뒤의 섹션은 더 위로 가지 못한다.
  assert.equal(canMoveUp(sections, 'treatments'), false)
  assert.equal(canMoveDown(sections, 'facts'), false)
  assert.equal(canMoveDown(sections, 'faq'), true)
})

test('fixed and untitled sections', () => {
  assert.equal(canDisable(section('doctor')), false)
  assert.equal(canDisable(section('faq')), true)
  assert.equal(canRetitle(section('facts')), false)
  assert.equal(canRetitle(section('treatments')), true)
})

test('draft expectation and discard path carry the draft identity', () => {
  assert.deepEqual(draftExpectation(null), { draft_id: null, revision: null })
  assert.deepEqual(draftExpectation({ id: 'd1', revision: 3 }), { draft_id: 'd1', revision: 3 })
  assert.equal(discardDraftPath('h1', { id: 'd1', revision: 3 }), '/admin/hospitals/h1/site-config/draft?draft_id=d1&revision=3')
})

test('GET state is read strictly and preview_enabled defaults to off', () => {
  const defaults = defaultsC()
  const state = readSiteConfigState({
    site_layout: 'C',
    published: null,
    draft: null,
    defaults: { A: defaults, B: defaults, C: defaults },
    defaults_version: '2026-11-21.1',
    history: [{ id: 'v1', state: 'SUPERSEDED', version: 1, layout: 'C', origin: 'EDIT', published_by: 'a', published_at: null, change_summary: ['faq'] }],
    warnings: [{ path: 'layout', code: 'SITE_LAYOUT_DIVERGED', message: '갈라짐' }],
  })
  assert.ok(state)
  assert.equal(state.preview_enabled, false)
  assert.equal(state.history[0].version, 1)
  assert.equal(state.warnings[0].code, 'SITE_LAYOUT_DIVERGED')
  assert.equal(readSiteConfigState({ defaults: { A: defaults } }), null)
})

test('write failures keep field errors and the latest draft', () => {
  const draftRow = {
    id: 'd2', state: 'DRAFT', version: null, layout: 'C', config: {}, resolved: defaultsC(), revision: 7,
  }
  const changed = readSiteConfigFailure(409, '충돌', { code: 'DRAFT_CHANGED', message: '바뀜', draft: draftRow })
  assert.equal(changed.kind, 'draft_changed')
  assert.equal(changed.kind === 'draft_changed' && changed.draft?.revision, 7)

  const invalid = readSiteConfigFailure(422, '검증', {
    code: 'SITE_CONFIG_INVALID',
    message: '설정에 고칠 곳이 있습니다.',
    errors: [{ path: 'sections[1].title', code: 'FORBIDDEN_EXPRESSION', message: '금지', words: ['최고'] }],
    warnings: [],
  })
  assert.equal(invalid.kind, 'invalid')
  if (invalid.kind === 'invalid') {
    assert.deepEqual(invalid.errors[0].words, ['최고'])
    const overrides = { sections: [{ id: 'treatments' }, { id: 'faq', title: '최고' }] }
    assert.equal(issuesForSection(invalid.errors, overrides, 'faq').length, 1)
    assert.equal(issuesForSection(invalid.errors, overrides, 'treatments').length, 0)
  }

  assert.equal(readSiteConfigFailure(503, '점검', { code: 'SITE_PREVIEW_DISABLED', message: '꺼짐' }).kind, 'preview_disabled')
  assert.equal(readSiteConfigFailure(500, '서버 오류', null).kind, 'other')
})

test('history change keys read as screen names, never raw keys', async () => {
  const { ADMIN_COPY } = await import('./admin-copy.ts')
  const { changeLabels } = await import('./site-config.ts')
  const labels = changeLabels(['sections.faq', 'sections.order', 'hero', 'brand_new', 'other_new'], ADMIN_COPY.siteEditor)
  assert.deepEqual(labels, ['자주 묻는 질문', '섹션 순서', '첫 화면', '기타 설정'])
  // 서버가 내는 모든 묶음 키에 이름이 있다(`site_defaults._COMMON` + layout).
  const source = readFileSync(new URL('../../backend/app/services/site_defaults.py', import.meta.url), 'utf8')
  const common = source.slice(source.indexOf('_COMMON: Final'), source.indexOf('_LAYOUT_OVERRIDES'))
  const groups = [...common.matchAll(/^    "([a-z_]+)": /gm)].map((m) => m[1])
  assert.ok(groups.length >= 10)
  for (const group of [...groups, 'layout']) assert.ok(group in ADMIN_COPY.siteEditor.changeKeys, group)
})

test('line break rules mirror the server', async () => {
  const { linesMatch, lineBreakProblem, splitLines, lineBreakSource, LINE_BREAK_PATHS, COPY_KEYS_BY_SECTION } = await import('./site-config.ts')
  const source = '365서귀포삼성정형외과 제주 서귀포 정형외과 전문의 진료'
  assert.equal(linesMatch('365서귀포삼성정형외과\n제주 서귀포 정형외과 전문의 진료', source), true)
  // 단어 중간 줄바꿈은 된다, 띄어쓰기를 바꾸거나 글자를 바꾸면 안 된다.
  assert.equal(linesMatch('365서귀포삼성\n정형외과 제주 서귀포 정형외과 전문의 진료', source), true)
  assert.equal(linesMatch('365서귀포삼성정형외과제주 서귀포 정형외과 전문의 진료', source), false)
  assert.equal(linesMatch('365서귀포삼성정형외과\n제주 정형외과 전문의 진료', source), false)
  assert.equal(lineBreakProblem('가\n\n나', '가 나'), 'too_many')
  assert.deepEqual(splitLines('가\r\n나\n'), ['가', '나'])
  assert.equal(lineBreakSource('care_principles.cards[1].body', { care_principles: { cards: [null, { body: ' 설명 ' }] } }), ' 설명 ')
  assert.equal(lineBreakSource('hero.headline', { hero_headline: '  ' }), null)

  const schema = readFileSync(new URL('../../backend/app/services/site_config_schema.py', import.meta.url), 'utf8')
  const copyKeys = [...(schema.match(/COPY_KEYS: tuple\[str, \.\.\.\] = \(([\s\S]*?)\n\)/)?.[1] ?? '').matchAll(/"([a-z_.]+)"/g)].map((m) => m[1]).sort()
  assert.deepEqual(Object.values(COPY_KEYS_BY_SECTION).flat().sort(), copyKeys)
  assert.match(schema, /"hero\.headline",\n    "hero\.description",\n    "care_principles\.title",\n    "care_principles\.lede"/)
  assert.equal(LINE_BREAK_PATHS.length, 4 + 3 * 2)
  assert.match(readFileSync(new URL('../../backend/app/services/clinic_page_copy.py', import.meta.url), 'utf8'), /CARE_PRINCIPLE_CARD_COUNT\s*(:\s*\w+)?\s*=\s*3/)
})

test('every server issue path lands on one screen spot', async () => {
  const { issueOwner } = await import('./site-config.ts')
  const overrides = { sections: [{ id: 'treatments' }, { id: 'faq', title: '최고' }] }
  const cases: [string, string | null][] = [
    ['sections[1].title', 'faq'],
    ['sections.treatments', 'treatments'],
    ['sections.1.title', 'faq'],
    ['copy.treatments.note', 'treatments'],
    ['copy.hours.weekend_label', 'hours'],
    ['care_flow.steps[0].title', 'care_flow'],
    ['care_flow.note', 'care_flow'],
    ['hero.line_breaks.hero.headline.pc', 'hero'],
    ['hero.line_breaks.care_principles.cards[0].title.mobile', 'care_principles'],
    ['faq.content_ids[0]', 'faq'],
    ['share.image_asset_id', 'page'],
    ['nav.items.faq', 'page'],
    ['layout', null],
    ['defaults_version', null],
  ]
  for (const [path, owner] of cases) assert.equal(issueOwner(path, overrides), owner, path)
})

test('line break whitespace follows python str.split/strip, not JS \\s', async () => {
  const { linesMatch, splitLines } = await import('./site-config.ts')
  assert.equal(linesMatch('가\n나', '가\u0085나'), true)
  assert.equal(linesMatch('가\n나', '가\uFEFF나'), false)
  assert.deepEqual(splitLines('\uFEFF가'), ['\uFEFF가'])
})

test('admin CSP lets the editor frame the site preview origin, nothing else', async () => {
  const { createRequire } = await import('node:module')
  const require = createRequire(import.meta.url)
  const domain = readFileSync(new URL('./platform-domain.ts', import.meta.url), 'utf8')
  const defaultHost = /const DEFAULT_SITE_HOST = '([^']+)'/.exec(domain)?.[1]
  const previous = process.env.NEXT_PUBLIC_SITE_URL
  try {
    for (const [env, origin] of [['', `https://${defaultHost}`], ['https://reputation.co.kr/x', 'https://reputation.co.kr']] as const) {
      process.env.NEXT_PUBLIC_SITE_URL = env
      const path = require.resolve('../next.config.js')
      delete require.cache[path]
      const config = require(path)
      const headers = await config.headers()
      const csp = headers[0].headers.find((h: { key: string }) => h.key === 'Content-Security-Policy').value as string
      assert.match(csp, new RegExp(`frame-src 'self' blob: ${origin.replace(/\./g, '\\.')}(;|$)`))
      assert.match(csp, /frame-ancestors 'none'/)
    }
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_SITE_URL
    else process.env.NEXT_PUBLIC_SITE_URL = previous
  }
})

test('the B-only fields match the backend B_LAYOUT_ONLY rule; only B shows them', () => {
  const source = readFileSync(new URL('../../backend/app/services/site_config_validation.py', import.meta.url), 'utf8')
  const pairs = [...(source.match(/_B_ONLY_FIELDS[^=]*= \(([\s\S]*?)\n\)/)?.[1] ?? '').matchAll(/\("([a-z_]+)", "([a-z_]+)"\)/g)].map(
    (m) => `${m[1]}.${m[2]}`,
  )
  // 서버 규칙이 아직 없는 main에서는 비교하지 않는다(#358이 함께 나간다).
  if (pairs.length) assert.deepEqual(pairs.sort(), B_ONLY_FIELDS.map(([group, key]) => `${group}.${key}`).sort())
  assert.equal(showsBOnlyFields('B'), true)
  assert.equal(showsBOnlyFields('A'), false)
  assert.equal(showsBOnlyFields('C'), false)
})

test('A and C never save the B-only fields, even when an old draft holds them', () => {
  const edited = clone(defaultsC())
  ;(edited.hero as { blocks: string[]; title_size: string }).blocks = ['phone', 'location']
  edited.treatments = { show_description: false, style: 'list' }
  const base = clone(defaultsC())
  base.treatments = { show_description: true, style: 'cards' }
  // 정보 블록만 바뀐 첫 화면·모양과 설명만 바뀐 진료 항목은 통째로 빠진다.
  assert.deepEqual(buildOverrides(edited, base, 'C'), {})
  ;(edited.hero as { title_size: string }).title_size = 'l'
  assert.deepEqual(buildOverrides(edited, base, 'C'), { hero: { title_size: 'l' } })
  // 레이아웃을 넘기지 않으면 완성본의 레이아웃(C)을 쓴다.
  assert.deepEqual(buildOverrides(edited, base), { hero: { title_size: 'l' } })
  // B는 그대로 싣는다.
  assert.deepEqual(buildOverrides(edited, base, 'B'), {
    hero: { title_size: 'l', blocks: ['phone', 'location'] },
    treatments: { show_description: false, style: 'list' },
  })
})

test('an old A/C draft holding B-only values counts as changed, so it can be saved clean', () => {
  const base = clone(defaultsC())
  base.treatments = { show_description: true, style: 'cards' }
  const stored = clone(base)
  ;(stored.hero as { blocks: string[] }).blocks = ['phone', 'location']
  // 서버에 저장된 모양(기준)과 편집기가 저장할 모양이 다르다 → '바뀜'.
  assert.notEqual(overridesCompareKey('C', stored, base, true), overridesCompareKey('C', stored, base))
  // B 전용 값이 없으면 둘이 같다 → '바뀜' 아님.
  assert.equal(overridesCompareKey('C', base, base, true), overridesCompareKey('C', base, base))
  // B는 두 모양이 언제나 같다.
  assert.equal(overridesCompareKey('B', stored, base, true), overridesCompareKey('B', stored, base))
})

test('the unrendered fields and fixed backgrounds match the backend rules', () => {
  const source = readFileSync(new URL('../../backend/app/services/site_config_validation.py', import.meta.url), 'utf8')
  const block = source.match(/_UNRENDERED_FIELDS[^=]*= \(([\s\S]*?)\n\)/)?.[1] ?? ''
  const pairs = [...block.matchAll(/\("([a-z_]+)", "([a-z_]+)"\)/g)].map((m) => `${m[1]}.${m[2]}`)
  assert.ok(pairs.length > 0, 'backend _UNRENDERED_FIELDS not found')
  assert.deepEqual(pairs.sort(), UNRENDERED_FIELDS.map(([g, k]) => `${g}.${k}`).sort())
  for (const layout of ['A', 'B', 'C'] as const) {
    const line = source.match(new RegExp(`"${layout}": frozenset\\(\\{([^}]*)\\}\\)`))?.[1] ?? ''
    const ids = [...line.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]).sort()
    assert.deepEqual(ids, [...BACKGROUND_FIXED[layout]].sort(), layout)
  }
})

test('head-less and fixed-background sections hide their look fields', () => {
  assert.deepEqual(sectionLookShown('B', 'hero'), { enabled: true, align: false, label: false, background: false })
  assert.deepEqual(sectionLookShown('B', 'section_index'), { enabled: false, align: false, label: false, background: false })
  assert.deepEqual(sectionLookShown('A', 'hours'), { enabled: true, align: true, label: true, background: false })
  assert.deepEqual(sectionLookShown('B', 'treatments'), { enabled: true, align: true, label: true, background: true })
})

test('hidden fields never reach the save, but a stored draft holding them counts as changed', () => {
  const base = clone(defaultsC())
  ;(base as Record<string, unknown>).doctor = { show_philosophy: false, photo_ratio: 'portrait' }
  ;(base as Record<string, unknown>).nav = { items: [], section_index: false }
  const stored = clone(base)
  ;(stored as Record<string, any>).doctor.show_philosophy = true
  ;(stored as Record<string, any>).nav.section_index = true
  stored.sections = stored.sections.map((section) =>
    section.id === 'hero' ? { ...section, align: 'center' as const } : section.id === 'care_principles' ? { ...section, background: 'tint' as const } : section,
  )
  assert.deepEqual(buildOverrides(stored, base, 'C'), {})
  assert.notEqual(overridesCompareKey('C', stored, base, true), overridesCompareKey('C', stored, base))
  const kept = buildOverrides(stored, base, 'C', true)
  assert.deepEqual((kept.doctor as Record<string, unknown>).show_philosophy, true)
  assert.ok(Array.isArray(kept.sections))
})

test('the shortcut menu cannot be turned on from the section list (the site does not draw it)', () => {
  assert.equal(canDisable(section('section_index')), false)
  assert.equal(canDisable(section('gallery')), true)
})

test('the lede line break is B-only: hidden on A·C, never saved there, and matches the backend list', () => {
  assert.equal(lineBreakPathShown('B', 'care_principles.lede'), true)
  assert.equal(lineBreakPathShown('A', 'care_principles.lede'), false)
  assert.equal(lineBreakPathShown('C', 'care_principles.title'), true)
  const base = clone(defaultsC())
  const edited = clone(base)
  ;(edited as Record<string, any>).hero = {
    ...(edited as Record<string, any>).hero,
    line_breaks: { 'care_principles.lede': { pc: 'a\nb', mobile: null }, 'hero.headline': { pc: 'h\ni', mobile: null } },
  }
  assert.deepEqual((buildOverrides(edited, base, 'C').hero as Record<string, any>).line_breaks, {
    'hero.headline': { pc: 'h\ni', mobile: null },
  })
  ;(edited as Record<string, any>).hero.line_breaks = { 'care_principles.lede': { pc: 'a\nb', mobile: null } }
  assert.equal(buildOverrides(edited, base, 'C').hero, undefined)
  assert.ok(((buildOverrides(edited, base, 'C', true).hero as Record<string, any>).line_breaks as object))
  const source = readFileSync(new URL('../../backend/app/services/site_config_validation.py', import.meta.url), 'utf8')
  const line = source.match(/_B_ONLY_LINE_BREAKS[^=]*= frozenset\(\{([^}]*)\}\)/)?.[1] ?? ''
  assert.deepEqual([...line.matchAll(/"([a-z_.]+)"/g)].map((m) => m[1]).sort(), [...B_ONLY_LINE_BREAK_PATHS].sort())
})

test('the weekend label copy is hidden, never saved, and matches the backend list', () => {
  assert.deepEqual([...shownCopyKeys('hours')], [])
  assert.deepEqual([...shownCopyKeys('faq')], ['faq.note'])
  const base = clone(defaultsC())
  const edited = clone(base)
  ;(edited as Record<string, any>).copy = { 'hours.weekend_label': '토요일 진료', 'faq.note': '안내' }
  assert.deepEqual(buildOverrides(edited, base, 'C').copy, { 'faq.note': '안내' })
  assert.deepEqual((buildOverrides(edited, base, 'C', true).copy as Record<string, string>)['hours.weekend_label'], '토요일 진료')
  const source = readFileSync(new URL('../../backend/app/services/site_config_validation.py', import.meta.url), 'utf8')
  const line = source.match(/_UNRENDERED_COPY_KEYS[^=]*= frozenset\(\{([^}]*)\}\)/)?.[1] ?? ''
  assert.deepEqual([...line.matchAll(/"([a-z_.]+)"/g)].map((m) => m[1]).sort(), [...UNRENDERED_COPY_KEYS].sort())
})
