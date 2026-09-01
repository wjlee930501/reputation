import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { countLabel, previewCountLabel } from './clinic-counters.ts'

const HERE = dirname(fileURLToPath(import.meta.url))

test('counts carry their unit and never go negative', () => {
  assert.equal(countLabel(12, '개'), '12개')
  assert.equal(countLabel(1, '편'), '1편')
  assert.equal(countLabel(0, '장'), '0장')
  assert.equal(countLabel(-3, '편'), '0편')
})

test('a preview label appears only when something is actually hidden', () => {
  assert.equal(previewCountLabel(6, 8, '장'), '전체 8장 중 6장')
  assert.equal(previewCountLabel(8, 8, '장'), null)
  // 전체가 보여준 수보다 작게 들어오면(집계 불일치) 더 보여줄 것이 없다고 말한다.
  assert.equal(previewCountLabel(8, 3, '장'), null)
})

test('the public surfaces that show a count go through this helper', () => {
  // P-C-3 — 같은 뜻의 숫자가 화면마다 다른 문장으로 적혀 있었다. 문자열 보간으로
  // 단위를 직접 붙이면 그 화면만 다시 갈라진다.
  const files = [
    join(HERE, '..', 'app', '[slug]', '_components', 'TreatmentGrid.tsx'),
    join(HERE, '..', 'app', '[slug]', '_components', 'ClinicGallery.tsx'),
    join(HERE, '..', 'app', '[slug]', '_components', 'DoctorIntro.tsx'),
    join(HERE, '..', 'app', '[slug]', 'treatments', 'page.tsx'),
    // 유형 필터(?type=)가 ISR을 막지 않도록 목록·필터 렌더링은 별도 컴포넌트로
    // 옮겼다(contents/page.tsx 자체는 개수 표기를 하지 않는다) — 상세는
    // app/[slug]/contents/_components/ContentsFeedView.tsx 참고.
    join(HERE, '..', 'app', '[slug]', 'contents', '_components', 'ContentsFeedView.tsx'),
    join(HERE, '..', 'app', '[slug]', 'treatments', '[treatmentSlug]', 'page.tsx'),
  ]

  for (const file of files) {
    const source = readFileSync(file, 'utf8')
    assert.match(source, /clinic-counters/, `${file}가 개수 표기 헬퍼를 쓰지 않습니다`)
    const inlineUnit = source.match(/\}(?:개|편|장)(?![가-힣])/g) ?? []
    assert.deepEqual(inlineUnit, [], `${file}에 단위를 직접 붙인 개수 표기가 남아 있습니다`)
  }
})
