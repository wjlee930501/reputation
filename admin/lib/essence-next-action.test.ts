import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  ESSENCE_SECTIONS,
  essenceAutoReviewBlockReasons,
  formatEssenceNextAction,
  resolveEssenceNextAction,
} from './essence-next-action.ts'

const base = {
  textSourceCount: 3,
  processedTextCount: 3,
  hasSelectedDraft: true,
  hasApproved: false,
  selectedIsReviewDraft: false,
}

test('every guidance step points at a section that exists on screen', () => {
  const states = [
    { ...base, textSourceCount: 0, processedTextCount: 0, hasSelectedDraft: false },
    { ...base, processedTextCount: 0, hasSelectedDraft: false },
    { ...base, hasSelectedDraft: false },
    { ...base, selectedIsReviewDraft: true },
  ]

  const sections = Object.values(ESSENCE_SECTIONS)
  for (const state of states) {
    const action = resolveEssenceNextAction(state)
    assert.ok(action, JSON.stringify(state))
    assert.ok(sections.includes(action.section as never), `section ${action.section}`)
  }
})

test('the blocked draft step is section 3 — there is no fourth section', () => {
  const action = resolveEssenceNextAction({ ...base, selectedIsReviewDraft: true })

  assert.equal(action?.section, ESSENCE_SECTIONS.REVIEW)
  assert.equal(formatEssenceNextAction(action!), '3단계 — AI 안전 검수가 보류한 최신 초안의 근거와 차단 사유를 확인하세요.')
})

test('the guidance walks input, extract, draft in order', () => {
  assert.equal(
    formatEssenceNextAction(
      resolveEssenceNextAction({ ...base, textSourceCount: 0, processedTextCount: 0, hasSelectedDraft: false })!,
    ),
    '1단계 — 근거로 쓸 자료를 1개 이상 입력하세요.',
  )
  assert.equal(
    formatEssenceNextAction(
      resolveEssenceNextAction({ ...base, processedTextCount: 0, hasSelectedDraft: false })!,
    ),
    '2단계 — 자료의 [근거 추출]을 실행하세요.',
  )
  assert.equal(
    formatEssenceNextAction(resolveEssenceNextAction({ ...base, hasSelectedDraft: false })!),
    '2단계 — 처리한 자료를 선택하고 [선택한 자료로 초안 만들기]를 누르세요.',
  )
})

test('a running standard has no section number to send the operator to', () => {
  const action = resolveEssenceNextAction({ ...base, hasApproved: true, hasSelectedDraft: false })

  assert.equal(action?.section, null)
  assert.equal(formatEssenceNextAction(action!), '운영 중 — 새 자료를 추가하면 새 버전 초안을 만들 수 있습니다.')
})

test('photos alone never satisfy the first step, because the count excludes them', () => {
  // 사진만 올린 병원은 textSourceCount가 0이므로 여전히 1단계다.
  const action = resolveEssenceNextAction({
    ...base,
    textSourceCount: 0,
    processedTextCount: 0,
    hasSelectedDraft: false,
  })

  assert.equal(action?.section, ESSENCE_SECTIONS.INPUT)
})

test('only the automatic review findings are read as block reasons', () => {
  const reasons = essenceAutoReviewBlockReasons([
    { field: 'patient_promise', reason: '근거 없음' },
    { field: 'automatic_ai_review', reason: '환자 약속 문장이 근거 노트와 어긋납니다.' },
    { field: 'automatic_ai_review', reason: '   ' },
    { field: 'automatic_ai_review', reason: '의료광고 금지 표현이 남아 있습니다.' },
  ])

  assert.deepEqual(reasons, [
    '환자 약속 문장이 근거 노트와 어긋납니다.',
    '의료광고 금지 표현이 남아 있습니다.',
  ])
})

test('a draft with no findings has no block reasons', () => {
  assert.deepEqual(essenceAutoReviewBlockReasons(null), [])
  assert.deepEqual(essenceAutoReviewBlockReasons([]), [])
  assert.deepEqual(essenceAutoReviewBlockReasons([{ field: 'doctor_voice', reason: 'x' }]), [])
})

test('the screen shows the block reason next to the guidance, not only deep in a panel', () => {
  const page = readFileSync(
    new URL('../app/hospitals/[id]/essence/page.tsx', import.meta.url),
    'utf8',
  )

  assert.match(page, /essenceAutoReviewBlockReasons/)
  assert.match(page, /formatEssenceNextAction/)
  // 화면에 없는 ④번 섹션을 다시 부르면 실패한다.
  assert.doesNotMatch(page, /'④/)
  assert.doesNotMatch(page, /④ AI/)
})
