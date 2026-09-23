import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

import nextConfig from '../next.config.mjs'
import { DIAGNOSIS_RETIRED_MESSAGE } from './diagnosis-retired.ts'

/**
 * 무료 진단 셀프 신청은 닫혀 있어야 한다. 진단 리포트는 도입문의 뒤 담당 마케터가 만든다.
 * 신청 화면이 되살아나거나 접수 API가 다시 백엔드로 이어지면, 자동 발송(연락 없이 리포트만
 * 받고 끝나는 경로)이 조용히 다시 열린다.
 */

test('the self-serve diagnosis page sends visitors to the inquiry', async () => {
  const redirects = await nextConfig.redirects!()
  const rule = redirects.find((r) => r.source === '/ai-diagnosis')
  assert.ok(rule, '/ai-diagnosis 리다이렉트가 없습니다.')
  assert.equal(rule.destination, '/contact')
  // 결과 확인 경로는 살아 있어야 한다.
  assert.ok(!redirects.some((r) => r.source.startsWith('/ai-diagnosis/')))
  assert.ok(existsSync(new URL('../app/ai-diagnosis/status/[token]/page.tsx', import.meta.url)))
  assert.ok(!existsSync(new URL('../app/ai-diagnosis/page.tsx', import.meta.url)))
})

test('the intake API no longer forwards to the backend', () => {
  const source = readFileSync(new URL('../app/api/diagnosis/route.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /getApiBase|fetch\(/)
  assert.match(source, /status: 410/)
})

test('the retirement notice points to the inquiry without an auto-send promise', () => {
  assert.match(DIAGNOSIS_RETIRED_MESSAGE, /도입문의/)
  assert.match(DIAGNOSIS_RETIRED_MESSAGE, /담당 마케터/)
  assert.doesNotMatch(DIAGNOSIS_RETIRED_MESSAGE, /15분|이메일로|자동/)
})
