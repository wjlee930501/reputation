import assert from 'node:assert/strict'
import test from 'node:test'

import {
  answerDemo,
  answerExamples,
  comparisonItems,
  landingHero,
  processSteps,
  proofItems,
  trustItems,
} from './landing-copy.ts'

test('landing hero leads with the second-homepage positioning', () => {
  const heroText = [
    landingHero.titleLead,
    landingHero.titleMain,
    landingHero.titleSupport,
    landingHero.body,
  ].join(' ')

  assert.match(heroText, /두 번째 홈페이지/)
  assert.match(heroText, /AE/)
  assert.match(heroText, /대신 운영/)
})

test('landing sections distinguish managed website operation from agent tooling', () => {
  const pageText = [
    ...proofItems.map((item) => `${item.title} ${item.body}`),
    ...comparisonItems.flatMap((item) => [item.label, item.title, ...item.points]),
    ...processSteps.map((item) => `${item.title} ${item.body}`),
    ...trustItems.map((item) => `${item.title} ${item.body}`),
  ].join(' ')

  assert.match(pageText, /AI가 읽을 수 있는/)
  assert.match(pageText, /툴을 배울 필요/)
  assert.match(pageText, /월간 리포트/)
})

test('answer demo is framed as an example without guaranteeing results (medical ad law)', () => {
  assert.match(answerDemo.disclaimer, /예시/)
  assert.match(answerDemo.disclaimer, /보장되지 않/)
  // 답변 예시는 실제 병원명이 아닌 플레이스홀더(○○)를 사용한다.
  assert.match(answerDemo.answerClinic, /○○/)
})

test('every specialty answer example stays a safe placeholder example', () => {
  assert.ok(answerExamples.length >= 3)
  for (const example of answerExamples) {
    assert.ok(example.tag.length > 0)
    assert.ok(example.question.length > 0)
    // 모든 진료과 예시도 실제 상호가 아닌 ○○ 플레이스홀더만 사용한다.
    assert.match(example.answerClinic, /○○/)
    // 근거 출처가 최소 1개 이상 붙는다.
    assert.ok(example.answerSources.length >= 1)
  }
})

test('landing copy avoids forbidden medical ad certainty wording', () => {
  const pageText = [
    landingHero.titleLead,
    landingHero.titleMain,
    landingHero.titleSupport,
    landingHero.body,
    landingHero.primaryCta,
    landingHero.secondaryCta,
    ...proofItems.flatMap((item) => [item.title, item.body]),
    ...comparisonItems.flatMap((item) => [item.label, item.title, ...item.points]),
    ...processSteps.flatMap((item) => [item.title, item.body]),
    ...trustItems.flatMap((item) => [item.title, item.body]),
    answerDemo.disclaimer,
    ...answerExamples.flatMap((item) => [
      item.tag,
      item.question,
      item.answerIntro,
      item.answerClinic,
      item.answerReason,
      ...item.answerSources,
    ]),
  ].join(' ')

  assert.doesNotMatch(pageText, /검증된/)
})
