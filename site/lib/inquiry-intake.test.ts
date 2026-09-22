import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { parseKeywords } from './diagnosis-form.ts'

/**
 * 도입문의 접수 계약.
 *
 * 폼이 접수되는 것만으로는 부족하다 — 진료과·지역·핵심 키워드가 백엔드까지 닿아야
 * `LeadCreate.diagnosis_input`이 초도 노출 진단을 만들고, 그래야 AE가 연락할 근거가
 * 생긴다. 셋 중 하나라도 프록시에서 떨어지면 "문의는 들어왔는데 보고서는 없는" 상태가
 * 조용히 계속된다. 라우트는 Next 서버 모듈이라 여기서는 소스 계약으로 고정한다
 * (diagnosis-proxy-paths.test.ts와 같은 방식).
 */
const ROUTE = readFileSync(join(process.cwd(), 'app/api/leads/route.ts'), 'utf8')

test('the intake proxy forwards every field the auto-diagnosis needs', () => {
  for (const field of ['specialty', 'region_keyword: regionKeyword', 'core_keywords: coreKeywords']) {
    assert.ok(ROUTE.includes(field), `백엔드로 보내는 본문에 ${field}가 없습니다.`)
  }
})

test('the intake proxy refuses a submission that cannot start a diagnosis', () => {
  // 셋이 필수가 아니면 폼은 통과하고 진단은 만들어지지 않는다.
  for (const field of ['specialty', 'regionKeyword', 'coreKeywords']) {
    assert.match(
      ROUTE,
      new RegExp(`REQUIRED_FIELDS[\\s\\S]*'${field}'[\\s\\S]*\\] as const`),
      `${field}가 필수 목록에 없습니다.`,
    )
  }
})

test('keyword parsing matches the backend clean_keywords contract', () => {
  // 백엔드는 빈 값·중복을 걷고 순서를 유지한 채 4개로 자른다.
  assert.deepEqual(parseKeywords('대장내시경, 치질, , 치질, 탈장, 맹장, 담석'), [
    '대장내시경',
    '치질',
    '탈장',
    '맹장',
  ])
  assert.deepEqual(parseKeywords('   '), [])
})

test('patient-sensitive text is screened on the diagnosis inputs too', () => {
  // question만 검사하면 진료과·지역·키워드 칸으로 같은 내용이 Slack과 Admin에 나간다.
  assert.match(ROUTE, /const freeText = \[question, specialty, regionKeyword, contactName, \.\.\.coreKeywords\]/)
})
