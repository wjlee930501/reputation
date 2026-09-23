import assert from 'node:assert/strict'
import test from 'node:test'

import { parseKeywords } from './diagnosis-form.ts'
import { leadSafetyError } from './lead-safety.ts'
import { POST } from './leads-route.ts'

/**
 * 도입문의 접수 계약.
 *
 * 폼이 접수되는 것만으로는 부족하다 — 진료과·지역·핵심 키워드가 백엔드까지 닿아야
 * `LeadCreate.diagnosis_input`이 초도 노출 진단을 만들고, 그래야 AE가 연락할 근거가
 * 생긴다. 필수 목록과 백엔드로 보내는 본문은 `leads-route.test.ts`가 실제 요청으로
 * 고정한다. 여기서는 키워드 정리 규칙과 진단 입력의 개인정보 검사를 고정한다.
 */

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

test('patient-sensitive text is screened on the diagnosis inputs too', async () => {
  // question만 검사하면 진료과·지역·키워드 칸으로 같은 내용이 Slack과 Admin에 나간다.
  const originalFetch = globalThis.fetch
  let upstreamCalled = false
  globalThis.fetch = (async () => {
    upstreamCalled = true
    throw new Error('upstream must not be called')
  }) as typeof fetch

  try {
    for (const field of ['specialty', 'regionKeyword', 'coreKeywords']) {
      const formData = new FormData()
      const fields: Record<string, string> = {
        clinicName: 'OOO정형외과',
        clinicAddress: '서울 강남구 테헤란로 00',
        directorName: '김원장',
        directorPhone: '010-1234-5678',
        homepage: 'https://clinic.example.com',
        specialty: '정형외과',
        regionKeyword: '강남역',
        coreKeywords: '도수치료, 허리통증',
        [field]: '주민번호 900101-1234567',
      }
      for (const [name, value] of Object.entries(fields)) formData.set(name, value)
      formData.set('privacy', 'on')
      formData.set('website', '')

      const response = await POST(
        new Request('https://site.example.com/api/leads', {
          method: 'POST',
          headers: { Accept: 'application/json' },
          body: formData,
        }),
      )
      const body = (await response.json()) as { error: string }

      assert.equal(response.status, 400, `${field}의 개인정보가 걸러지지 않았습니다.`)
      assert.equal(body.error, leadSafetyError())
    }
    assert.equal(upstreamCalled, false)
  } finally {
    globalThis.fetch = originalFetch
  }
})
