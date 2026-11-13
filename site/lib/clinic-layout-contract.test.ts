// 병원 사이트 레이아웃 A/B/C의 화면 계약 (docs/plans/2026-11-13-clinic-site-layouts-abc.md §1).
//
// 레이아웃은 표현만 바꾼다. 구조화 데이터와 화면이 같은 사실을 말한다는 계약(FAQ = FAQPage,
// 질문 목록과 FAQ가 겹치지 않음, 의료진 전원, 진료 항목 링크, 갤러리 링크, 사실 패널)은
// 레이아웃 도입 전 화면과 똑같이 지켜야 한다. schema.test.ts가 레이아웃 도입 전 화면을
// 소스로 확인하듯, 여기서는 레이아웃 A·C 컴포넌트와 스타일시트를 확인한다.
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import postcss from 'postcss'

const HERE = dirname(fileURLToPath(import.meta.url))
const SLUG_DIR = join(HERE, '..', 'app', '[slug]')
const LAYOUT_DIR = join(SLUG_DIR, '_layouts')
const read = (...parts: string[]) => readFileSync(join(...parts), 'utf8')

const PAGE = read(SLUG_DIR, 'page.tsx')
const LAYOUTS = {
  A: read(LAYOUT_DIR, 'HomeLayoutA.tsx'),
  C: read(LAYOUT_DIR, 'HomeLayoutC.tsx'),
}

test('the page builds one home model from the same FAQ and question selections', () => {
  assert.match(PAGE, /const faqEntries = selectFaqEntries\(contents, hospitalRootUrl\)/)
  assert.match(PAGE, /const homeModel: ClinicHomeModel = \{[\s\S]*?faqEntries,[\s\S]*?clusterContents,[\s\S]*?\}/)
  assert.match(PAGE, /<HomeLayoutA model=\{homeModel\} \/>/)
  assert.match(PAGE, /<HomeLayoutC model=\{homeModel\} \/>/)
  // 레이아웃을 고르지 않은 병원(NULL)과 B는 레이아웃 도입 전 구성을 그대로 쓴다.
  assert.match(PAGE, /layout === 'A' \?[\s\S]*?layout === 'C' \?[\s\S]*?<ClinicFaq entries=\{faqEntries\}/)
})

for (const [name, source] of Object.entries(LAYOUTS)) {
  test(`layout ${name} renders exactly one h1`, () => {
    assert.equal(source.match(/<h1\b/g)?.length ?? 0, 1)
  })

  test(`layout ${name} shows the same FAQ entries it declares in JSON-LD`, () => {
    assert.match(source, /faqEntries\.map\(\(entry\) =>/)
    assert.match(source, /\{entry\.question\}/)
    assert.match(source, /\{entry\.answer\}/)
  })

  test(`layout ${name} picks questions only from content the FAQ section did not take`, () => {
    assert.match(source, /selectAnswerQuestions\(clusterContents,/)
    assert.doesNotMatch(source, /selectAnswerQuestions\(contents,/)
    // 콘텐츠가 적은 병원에서는 질문 목록을 끈다(레이아웃 도입 전 화면과 같은 구성 규칙).
    assert.match(source, /composition\.showAnswerClusters \?/)
  })

  test(`layout ${name} lists every physician`, () => {
    assert.match(source, /physicians\.map\(\(physician/)
    assert.match(source, /<PhysicianProfile/)
  })

  test(`layout ${name} links each treatment to its own page`, () => {
    assert.match(source, /hospital\.treatments\.map\(/)
    assert.match(source, /buildTreatmentSlug\(treatment\.name\)/)
    assert.match(source, /\$\{hospitalRootUrl\}\/treatments\/\$\{slug\}/)
  })

  test(`layout ${name} links the home gallery to the full photo library`, () => {
    assert.match(source, /clinicGalleryPolicy\('home', model\.contentDensity\)/)
    assert.match(source, /excludeUrl: model\.heroPhotoUrl/)
    assert.match(source, /\$\{hospitalRootUrl\}\/visit#gallery/)
  })

  test(`layout ${name} keeps the in-page anchors other sections link to`, () => {
    for (const id of ['treatments', 'doctor', 'facts', 'contact', 'faq']) {
      assert.match(source, new RegExp(`id="${id}"`), `id="${id}"가 없습니다`)
    }
  })

  test(`layout ${name} shows the hospital facts the schema and llms.txt carry`, () => {
    for (const field of ['hospital.name', 'hospital.phone', 'hospital.hira_org_id', 'hospital.director_name']) {
      assert.ok(source.includes(field), `${field}를 그리지 않습니다`)
    }
    assert.match(source, /fullClinicAddress\(hospital\.address, hospital\.address_detail\)/)
    assert.match(source, /model\.factLinks\.filter/)
  })

  test(`layout ${name} takes the shared copy from one place instead of retyping it`, () => {
    assert.match(source, /from '@\/lib\/clinic-template-copy'/)
    for (const phrase of ['충분히 설명합니다', '상담·문진', '경과 확인·생활 관리']) {
      assert.ok(!source.includes(phrase), `${phrase}를 컴포넌트에 직접 적었습니다`)
    }
  })

  test(`layout ${name} keeps the h1 text of the pre-layout page`, () => {
    assert.match(source, /buildClinicHeroHeadline\(\{/)
    // 앞부분이 없으면(한 조각 문구) 그 조각이 큰 제목이 된다 — h1 글자는 지금 화면과 같다.
    assert.match(source, /\(headline\.lead\.length > 0 \? headline\.lead : \[headline\.emphasis\]\)\.map/)
    assert.match(source, /\{headline\.emphasis\}/)
  })
}

test('the pre-layout home keeps rendering the template copy from the shared module', () => {
  const principles = read(SLUG_DIR, '_components', 'CarePrinciples.tsx')
  const flow = read(SLUG_DIR, '_components', 'CareFlow.tsx')
  assert.match(principles, /CARE_PRINCIPLES\.map/)
  assert.match(flow, /CARE_STEPS\.map/)
})

test('every page of a hospital follows the chosen layout shell', () => {
  const pages = [
    ['page.tsx'],
    ['doctor', 'page.tsx'],
    ['treatments', 'page.tsx'],
    ['treatments', '[treatmentSlug]', 'page.tsx'],
    ['contents', 'page.tsx'],
    ['contents', '[contentId]', 'page.tsx'],
    ['visit', 'page.tsx'],
  ]
  for (const parts of pages) {
    const source = read(SLUG_DIR, ...parts)
    assert.match(source, /clinicLayoutShellClass\(/, `${parts.join('/')}가 레이아웃 셸을 쓰지 않습니다`)
  }
})

// ── 레이아웃 스타일시트 ─────────────────────────────────────────

const MODULES = readdirSync(LAYOUT_DIR)
  .filter((file) => file.endsWith('.module.css'))
  .map((file) => ({ file, root: postcss.parse(read(LAYOUT_DIR, file), { from: file }) }))

test('the layout stylesheets are found', () => {
  assert.deepEqual(
    MODULES.map((module) => module.file).sort(),
    ['layout-a.module.css', 'layout-b.module.css', 'layout-c.module.css'],
  )
})

test('layout font weights snap to the declared steps', () => {
  const offenders: string[] = []
  for (const { file, root } of MODULES) {
    root.walkDecls('font-weight', (decl) => {
      const literal = /^(\d{3})$/.exec(decl.value.trim())
      if (literal && ![400, 500, 600, 700, 800].includes(Number(literal[1]))) {
        offenders.push(`${file}: ${decl.value}`)
      }
    })
  }
  assert.deepEqual(offenders, [])
})

test('layouts never put a shadow on the clinic surface', () => {
  const offenders: string[] = []
  for (const { file, root } of MODULES) {
    root.walkDecls(/^box-shadow$/, (decl) => {
      offenders.push(`${file}: ${decl.value}`)
    })
  }
  assert.deepEqual(offenders, [])
})

test('layout buttons use the shared control radius and weight tokens', () => {
  for (const { file, root } of MODULES) {
    root.walkRules((rule) => {
      // 선택자의 마지막 대상이 버튼 클래스인 규칙만 — `a:not(.primaryButton)`은 버튼이 아니다.
      if (!rule.selectors.some((selector) => /\.[A-Za-z]*Button$/.test(selector.trim()))) return
      rule.walkDecls('border-radius', (decl) => {
        assert.match(decl.value, /var\(--clinic-control-radius(-pill)?\)/, `${file} ${rule.selector}`)
      })
      rule.walkDecls('font-weight', (decl) => {
        assert.match(decl.value, /var\(--clinic-control-weight(-idle)?\)/, `${file} ${rule.selector}`)
      })
    })
  }
})

test('layout sections take their vertical rhythm from the shared section token', () => {
  const SECTIONS = ['.section', '.centerSection', '.infoRow', '.tintSection', '.darkSection']
  for (const { file, root } of MODULES) {
    for (const section of SECTIONS) {
      const vertical: string[] = []
      let present = false
      root.walkRules((rule) => {
        if (!rule.selectors.some((selector) => selector.trim() === section)) return
        present = true
        rule.walkDecls(/^padding(-top)?$/, (decl) => {
          vertical.push(decl.value)
        })
      })
      if (!present) continue
      assert.ok(
        vertical.some((value) => value.includes('var(--clinic-section-y')),
        `${file} ${section}의 세로 여백이 --clinic-section-y를 쓰지 않습니다`,
      )
    }
  }
})

/** clamp(34px, …)나 22px에서 보장되는 최소 크기. */
function minimumPx(value: string): number | null {
  const clamp = /^clamp\(\s*([\d.]+)px/.exec(value)
  if (clamp) return Number(clamp[1])
  const plain = /^([\d.]+)px$/.exec(value)
  return plain ? Number(plain[1]) : null
}

function fontSize(root: postcss.Root, selector: string): number {
  let found: number | null = null
  root.walkRules((rule) => {
    if (rule.selector !== selector) return
    rule.walkDecls('font-size', (decl) => {
      found = minimumPx(decl.value)
    })
  })
  assert.ok(found !== null, `${selector}의 font-size를 찾지 못했습니다`)
  return found
}

test('in every layout the h1 stays larger than h2, and h2 larger than h3, even on a phone', () => {
  const headingClasses: Record<string, { h1: string; h2: string[]; h3: string[] }> = {
    'layout-a.module.css': {
      h1: '.heroLead',
      h2: ['.bigTitle', '.bandTitle'],
      h3: ['.doctorBody h3', '.principleTitle', '.questionTitle', '.flowTitle', '.smallTitle'],
    },
    'layout-c.module.css': {
      h1: '.heroLead',
      h2: ['.title'],
      h3: ['.doctorBody h3', '.policyCard h3', '.flow h3', '.qaTitle'],
    },
  }
  for (const { file, root } of MODULES) {
    const classes = headingClasses[file]
    if (!classes) continue
    const h1 = fontSize(root, classes.h1)
    const h2 = Math.min(...classes.h2.map((selector) => fontSize(root, selector)))
    const h3 = Math.max(...classes.h3.map((selector) => fontSize(root, selector)))
    assert.ok(h1 > h2, `${file}: h1 ${h1}px ≤ h2 ${h2}px`)
    assert.ok(h2 > h3, `${file}: h2 ${h2}px ≤ h3 ${h3}px`)
  }
})
