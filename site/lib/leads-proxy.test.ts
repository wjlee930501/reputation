import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildInquiryLeadPayload,
  diagnosisInputError,
  isReasonableKoreanMobilePhone,
  isValidHttpUrl,
} from './inquiry-lead.ts'
import { buildLeadOutboundHeaders, isLeadValidationUpstreamStatus } from './leads-proxy.ts'

const ORIGINAL_BFF_SECRET = process.env.SITE_BFF_SECRET

test.afterEach(() => {
  if (ORIGINAL_BFF_SECRET === undefined) {
    Reflect.deleteProperty(process.env, 'SITE_BFF_SECRET')
  } else {
    process.env.SITE_BFF_SECRET = ORIGINAL_BFF_SECRET
  }
})

test('lead proxy sends BFF auth even when visitor IP cannot be parsed', () => {
  process.env.SITE_BFF_SECRET = 'site-bff-secret'

  const headers = buildLeadOutboundHeaders(new Headers())

  assert.equal(headers['X-BFF-Auth'], 'site-bff-secret')
  assert.equal(headers['X-Visitor-IP'], undefined)
})

test('lead proxy forwards visitor IP when trusted proxy headers are available', () => {
  process.env.SITE_BFF_SECRET = 'site-bff-secret'

  const headers = buildLeadOutboundHeaders(
    new Headers({ 'x-forwarded-for': '198.51.100.7, 203.0.113.10' }),
  )

  assert.equal(headers['X-BFF-Auth'], 'site-bff-secret')
  assert.equal(headers['X-Visitor-IP'], '198.51.100.7')
  assert.equal(headers['X-Forwarded-For'], '198.51.100.7')
})

test('lead proxy treats upstream auth failures as service errors, not input validation', () => {
  assert.equal(isLeadValidationUpstreamStatus(400), true)
  assert.equal(isLeadValidationUpstreamStatus(422), true)
  assert.equal(isLeadValidationUpstreamStatus(401), false)
  assert.equal(isLeadValidationUpstreamStatus(403), false)
  assert.equal(isLeadValidationUpstreamStatus(429), false)
})

test('inquiry fields map to the upstream lead fields with the 도입문의 marker kept', () => {
  assert.deepEqual(
    buildInquiryLeadPayload({
      clinicName: ' OOO정형외과 ',
      clinicAddress: ' 서울 강남구 테헤란로 00 ',
      directorName: ' 김원장 ',
      directorPhone: ' 010-1234-5678 ',
      homepage: ' https://clinic.example.com ',
      specialty: ' 정형외과 ',
      regionKeyword: ' 강남역 ',
      coreKeywords: ' 도수치료, 허리통증 ,, 도수치료, 무릎, 어깨, 발목 ',
    }),
    {
      clinic_name: 'OOO정형외과',
      clinic_type: '도입문의',
      contact: '010-1234-5678',
      contact_name: '김원장',
      specialty: '정형외과',
      region_keyword: '강남역',
      core_keywords: ['도수치료', '허리통증', '무릎', '어깨'],
      question: [
        '병원 주소: 서울 강남구 테헤란로 00',
        '원장님 성함: 김원장',
        '병원 홈페이지: https://clinic.example.com',
      ].join('\n'),
    },
  )
})

test('inquiry phone validation accepts reasonable Korean mobile formats', () => {
  assert.equal(isReasonableKoreanMobilePhone('010-1234-5678'), true)
  assert.equal(isReasonableKoreanMobilePhone('01012345678'), true)
  assert.equal(isReasonableKoreanMobilePhone('02-1234-5678'), false)
  assert.equal(isReasonableKoreanMobilePhone('010-12-5678'), false)
})

test('inquiry homepage validation requires an absolute HTTP(S) URL', () => {
  assert.equal(isValidHttpUrl('https://clinic.example.com'), true)
  assert.equal(isValidHttpUrl('http://clinic.example.com/path'), true)
  assert.equal(isValidHttpUrl('clinic.example.com'), false)
  assert.equal(isValidHttpUrl('javascript:alert(1)'), false)
  assert.equal(isValidHttpUrl(''), false)
})

test('diagnosis inputs are refused when empty or when they carry the clinic name', () => {
  const base = { clinicName: '장편한외과의원', specialty: '외과', regionKeyword: '수서역' }
  assert.equal(diagnosisInputError({ ...base, coreKeywords: '치질, 항문 통증' }), null)
  assert.equal(diagnosisInputError({ ...base, coreKeywords: ' , ' }), 'empty')
  assert.equal(diagnosisInputError({ ...base, coreKeywords: '장편한 외과 의원 치질' }), 'contains-clinic-name')
  assert.equal(diagnosisInputError({ ...base, regionKeyword: '장편한외과의원 근처', coreKeywords: '치질' }), 'contains-clinic-name')
})
