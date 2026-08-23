import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { buildFaqPageJsonLd, selectFaqEntries } from './schema.ts'

const HERE = dirname(fileURLToPath(import.meta.url))

function faq(id: number) {
  return {
    id: String(id),
    content_type: 'FAQ',
    title: `FAQ ${id}`,
    meta_description: `Answer ${id}`,
    image_url: null,
    scheduled_date: '2026-06-20',
    published_at: null,
    body_updated_at: null,
    references: [],
    faq_question: `Question ${id}`,
    faq_answer_summary: `Answer ${id}`,
  }
}

test('production FAQPage JSON-LD caps mainEntity at 10 entries', () => {
  const result = buildFaqPageJsonLd(
    Array.from({ length: 15 }, (_, i) => faq(i)),
    'https://clinic.example',
  )

  assert.ok(result)
  const mainEntity = result.mainEntity
  assert.ok(Array.isArray(mainEntity))
  assert.equal(mainEntity.length, 10)
})

test('only FAQ-type content with an approved question and answer becomes a Question', () => {
  const entries = selectFaqEntries(
    [
      { ...faq(1) },
      // 다른 유형의 제목은 질문이 아니다 — 의료 콘텐츠 범위를 벗어난다.
      { ...faq(2), content_type: 'COLUMN' },
      // 답변 요약이 없으면 내보낼 답이 없다.
      { ...faq(3), faq_answer_summary: null, meta_description: null },
      { ...faq(4), faq_question: '   ', title: '' },
    ],
    'https://clinic.example',
  )

  assert.deepEqual(entries.map((entry) => entry.id), ['1'])
  assert.equal(entries[0].question, 'Question 1')
  assert.equal(entries[0].answer, 'Answer 1')
  assert.equal(entries[0].url, 'https://clinic.example/contents/1')
})

test('a list title never stands in for an approved question', () => {
  // 제목은 질문 형태로 승인된 문장이 아니다. `faq_question || title`로 물러서면
  // 답변 엔진이 `치질 수술 안내`를 환자가 던진 질문으로 인용한다.
  const entries = selectFaqEntries(
    [{ ...faq(1), faq_question: null, title: '치질 수술 안내' }],
    'https://clinic.example',
  )
  assert.deepEqual(entries, [])
})

test('a meta description never stands in for a reviewed answer', () => {
  // meta_description은 검색 결과 요약이고 답변으로 검수된 적이 없다.
  const entries = selectFaqEntries(
    [{ ...faq(1), faq_answer_summary: null, meta_description: '수술 후 회복 안내 요약' }],
    'https://clinic.example',
  )
  assert.deepEqual(entries, [])
  assert.equal(
    buildFaqPageJsonLd(
      [{ ...faq(1), faq_answer_summary: null, meta_description: '수술 후 회복 안내 요약' }],
      'https://clinic.example',
    ),
    null,
  )
})

test('the selector reads only the two approved fields', () => {
  // 되돌리기 쉬운 한 줄이므로 소스에서도 fallback이 없음을 고정한다.
  const source = readFileSync(join(HERE, 'schema.ts'), 'utf8')
  const selector = source.slice(
    source.indexOf('export function selectFaqEntries'),
    source.indexOf('export function buildFaqPageJsonLd'),
  )
  assert.doesNotMatch(selector, /faq_question\s*\|\|/)
  assert.doesNotMatch(selector, /faq_answer_summary\s*\|\|/)
  assert.doesNotMatch(selector, /c\.title/)
  assert.doesNotMatch(selector, /c\.meta_description/)
})

test('the FAQPage node carries exactly what the visible section renders', () => {
  // P-A-5 — 구조화 데이터에만 있고 화면에는 없는 Q&A는 검색·답변 엔진이 신뢰하지
  // 않는다. 두 출력이 같은 선택 함수를 통과하는지 여기서 고정한다.
  const contents = Array.from({ length: 4 }, (_, i) => faq(i))
  const entries = selectFaqEntries(contents, 'https://clinic.example')
  const jsonLd = buildFaqPageJsonLd(contents, 'https://clinic.example')

  assert.ok(jsonLd)
  const mainEntity = jsonLd.mainEntity as Array<Record<string, unknown>>
  assert.deepEqual(
    mainEntity.map((question) => question.name),
    entries.map((entry) => entry.question),
  )
  assert.deepEqual(
    mainEntity.map((question) => (question.acceptedAnswer as Record<string, string>).text),
    entries.map((entry) => entry.answer),
  )
})

test('no FAQ at all means no FAQPage node', () => {
  assert.equal(buildFaqPageJsonLd([], 'https://clinic.example'), null)
  assert.equal(
    buildFaqPageJsonLd([{ ...faq(1), content_type: 'HEALTH' }], 'https://clinic.example'),
    null,
  )
})

test('the hospital home renders the same FAQ entries it declares in JSON-LD', () => {
  const page = readFileSync(join(HERE, '..', 'app', '[slug]', 'page.tsx'), 'utf8')
  assert.match(page, /const faqEntries = selectFaqEntries\(contents, hospitalRootUrl\)/)
  assert.match(page, /<ClinicFaq entries=\{faqEntries\}/)

  const component = readFileSync(
    join(HERE, '..', 'app', '[slug]', '_components', 'ClinicFaq.tsx'),
    'utf8',
  )
  // 질문과 답변 요약이 둘 다 화면에 있어야 구조화 데이터가 페이지를 뒷받침한다.
  assert.match(component, /\{entry\.question\}/)
  assert.match(component, /\{entry\.answer\}/)
})

test('the approved Q&A has one owner on the home, so no item renders twice', () => {
  // 질문 목록(AnswerClusters)은 FAQ를 우선순위 맨 앞에 두고 뽑기 때문에, 같은
  // 항목을 FAQ 섹션과 나란히 반복해서 보여주게 된다.
  const page = readFileSync(join(HERE, '..', 'app', '[slug]', 'page.tsx'), 'utf8')
  assert.match(page, /const faqEntryIds = new Set\(faqEntries\.map\(\(entry\) => entry\.id\)\)/)
  assert.match(
    page,
    /const clusterContents = contents\.filter\(\(content\) => !faqEntryIds\.has\(content\.id\)\)/,
  )
  assert.match(page, /<AnswerClusters\s+contents=\{clusterContents\}/)
  // 질문 목록에 원본 contents를 그대로 넘기면 중복이 돌아온다.
  assert.doesNotMatch(page, /<AnswerClusters\s+contents=\{contents\}/)
})

test('the two home question surfaces never select the same content', () => {
  // 컴포넌트 두 개의 선택 규칙을 함께 돌려 실제로 겹치지 않는지 확인한다.
  const contents = [
    faq(1),
    faq(2),
    { ...faq(3), content_type: 'DISEASE', faq_question: null, faq_answer_summary: null },
    { ...faq(4), content_type: 'TREATMENT', faq_question: null, faq_answer_summary: null },
    // 승인된 답변이 아직 없는 FAQ는 FAQ 섹션이 가져가지 않으므로 질문 목록에 남는다.
    { ...faq(5), faq_answer_summary: null, meta_description: 'summary' },
  ]

  const entries = selectFaqEntries(contents, 'https://clinic.example')
  const entryIds = new Set(entries.map((entry) => entry.id))
  const clusterContents = contents.filter((content) => !entryIds.has(content.id))

  assert.deepEqual([...entryIds], ['1', '2'])
  assert.deepEqual(clusterContents.map((content) => content.id), ['3', '4', '5'])
  for (const content of clusterContents) {
    assert.ok(!entryIds.has(content.id), `${content.id}가 두 섹션에 모두 나옵니다`)
  }
})
