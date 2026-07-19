import assert from 'node:assert/strict'
import test from 'node:test'

import { inferPillarTreatment } from './treatment-slug.ts'

test('authoritative query-target treatment wins before text heuristics', () => {
  const treatments = [
    { name: '어깨 통증', description: '어깨 진료' },
    { name: '척추관협착증', description: '척추 진료' },
  ]
  const content = {
    title: '어깨 통증처럼 느껴지는 다리 저림의 원인',
    meta_description: '걸을 때 심해지는 증상을 설명합니다.',
    faq_question: null,
    query_target_treatment: '척추관협착증',
  }

  assert.equal(inferPillarTreatment(treatments, content)?.name, '척추관협착증')
})

test('an authoritative but unavailable treatment never falls back to an unrelated title match', () => {
  const treatments = [{ name: '어깨 통증' }]
  const content = {
    title: '어깨 통증 치료 안내',
    query_target_treatment: '척추관협착증',
  }

  assert.equal(inferPillarTreatment(treatments, content), undefined)
})
