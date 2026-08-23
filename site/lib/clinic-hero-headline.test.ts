import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { buildClinicHeroHeadline } from './clinic-hero-headline.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const HERO = readFileSync(
  join(HERE, '..', 'app', '[slug]', '_components', 'ClinicHero.tsx'),
  'utf8',
)
const CSS = readFileSync(join(HERE, '..', 'app', 'globals.css'), 'utf8')

test('the headline text keeps a space between every part', () => {
  // 이 값이 붙어 나오는 것이 P-A-4의 본체다 — 답변 엔진과 스크린 리더가 읽는 문장이
  // `대장항문외과,의료진과 진료 정보를방문 전에 확인하세요`였다.
  const headline = buildClinicHeroHeadline({
    accessMode: 'appointment',
    specialtyLabel: '대장항문외과',
    hospitalName: '장편한외과의원',
  })

  assert.equal(headline.text, '대장항문외과, 의료진과 진료 정보를 방문 전에 확인하세요')
  assert.doesNotMatch(headline.text, /[가-힣],[가-힣]/)
  assert.doesNotMatch(headline.text, /를방문|을확인|고안내/)
})

test('the default copy is two parts, not a forced three-line split', () => {
  for (const accessMode of ['urgent', 'appointment', 'specialist'] as const) {
    const headline = buildClinicHeroHeadline({
      accessMode,
      specialtyLabel: '내과',
      hospitalName: '테스트의원',
    })
    assert.equal(headline.lead.length, 1, `${accessMode}의 기본 문구가 조각 수로 줄을 고정합니다`)
    assert.ok(headline.emphasis.length > 0)
    assert.equal(headline.text, `${headline.lead[0]} ${headline.emphasis}`)
  }
})

test('a clinic without an approved specialty label falls back to its own name', () => {
  const headline = buildClinicHeroHeadline({
    accessMode: 'appointment',
    specialtyLabel: '',
    hospitalName: '노원탑365의원',
  })
  assert.equal(headline.lead[0], '노원탑365의원')
})

test('an approved headline keeps its own line breaks as parts', () => {
  const headline = buildClinicHeroHeadline({
    approvedHeadline: '  증상을 정확히 확인하고\n\n필요한 치료만 안내합니다  ',
    accessMode: 'specialist',
    specialtyLabel: '내과',
    hospitalName: '테스트의원',
  })

  assert.deepEqual(headline.lead, ['증상을 정확히 확인하고'])
  assert.equal(headline.emphasis, '필요한 치료만 안내합니다')
  assert.equal(headline.text, '증상을 정확히 확인하고 필요한 치료만 안내합니다')
  // 운영자가 나눈 줄이므로 화면에서도 줄이 나뉘어야 한다 (L-3).
  assert.equal(headline.explicitLines, true)
})

test('only an operator-authored multi-line headline claims explicit lines', () => {
  // 자동 생성 문구는 조각이 둘이어도 폭에 맞춰 흘러야 한다 — 조각 수로 줄을 고정하던
  // P-A-4 회귀를 막는다.
  const generated = buildClinicHeroHeadline({
    accessMode: 'urgent',
    specialtyLabel: '정형외과',
    hospitalName: '노원탑365의원',
  })
  assert.equal(generated.lead.length, 1)
  assert.equal(generated.explicitLines, false)

  const singleLine = buildClinicHeroHeadline({
    approvedHeadline: '한 줄로 승인된 문장',
    accessMode: 'urgent',
    specialtyLabel: '정형외과',
    hospitalName: '노원탑365의원',
  })
  assert.equal(singleLine.explicitLines, false)
})

test('a single-line approved headline needs no lead part', () => {
  const headline = buildClinicHeroHeadline({
    approvedHeadline: '한 줄로 승인된 문장',
    accessMode: 'urgent',
    specialtyLabel: '내과',
    hospitalName: '테스트의원',
  })

  assert.deepEqual(headline.lead, [])
  assert.equal(headline.text, '한 줄로 승인된 문장')
})

test('the hero renders the separator and lets the browser choose the line breaks', () => {
  // 공백은 JSX에서 조각 사이에 명시적으로 넣어야 한다 — 줄바꿈만 두면 사라진다.
  assert.match(HERO, /\{part\}\{' '\}/)

  const rule = CSS.slice(CSS.indexOf('.clinic-hero-editorial-title {'))
  const body = rule.slice(0, rule.indexOf('\n}'))
  assert.match(body, /text-wrap:\s*balance/)
  // 조각을 블록으로 쌓으면 조각 수가 그대로 줄 수가 된다.
  assert.doesNotMatch(
    CSS.slice(
      CSS.indexOf('.clinic-hero-editorial-title span,'),
      CSS.indexOf('.clinic-hero-editorial-lede'),
    ),
    /display:\s*block/,
  )
})

test('an operator-authored headline gets the block-line modifier, and only it', () => {
  // 히어로가 모디파이어를 실제로 붙이는가.
  assert.match(HERO, /headline\.explicitLines/)
  assert.match(HERO, /clinic-hero-editorial-title--lines/)

  // 그 모디파이어에만 블록이 걸려 있는가 — 기본 규칙은 위 테스트가 지킨다.
  const modifier = CSS.slice(CSS.indexOf('.clinic-hero-editorial-title--lines span,'))
  assert.match(modifier.slice(0, modifier.indexOf('\n}')), /display:\s*block/)
})

test('an approved headline never renders more than three parts', () => {
  const headline = buildClinicHeroHeadline({
    approvedHeadline: ['하나', '둘', '셋', '넷', '다섯'].join('\n'),
    accessMode: 'appointment',
    specialtyLabel: '내과',
    hospitalName: '테스트의원',
  })

  assert.equal(headline.lead.length + 1, 3)
  assert.equal(headline.text, '하나 둘 셋')
})
