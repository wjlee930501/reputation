import assert from 'node:assert/strict'
import test from 'node:test'

import {
  hasStructuredInquiryDetails,
  isIntroductionInquiry,
  leadEmptyState,
  readInquiryDetails,
} from './lead-list.ts'

test('filtered-empty copy states the real total and differs from data-empty copy', () => {
  assert.deepEqual(leadEmptyState(true, 8), {
    title: '조건에 맞는 상담 요청이 없습니다 (전체 8건)',
    detail: '확인 필요는 신규 요청이거나 첫 연락 기한을 넘긴 미연락 상담 요청입니다.',
  })
  assert.equal(leadEmptyState(false, 0).title, '아직 접수된 상담 요청이 없습니다.')
})

test('introduction inquiries are identified by funnel source or explicit clinic type', () => {
  assert.equal(isIntroductionInquiry({ clinic_type: '도입문의', source: null }), true)
  assert.equal(isIntroductionInquiry({ clinic_type: '내과', source: 'INQUIRY' }), true)
  assert.equal(isIntroductionInquiry({ clinic_type: '내과', source: 'AI_DIAGNOSIS' }), false)
})

test('five-field inquiry question is parsed into labelled admin details', () => {
  assert.deepEqual(readInquiryDetails({
    contact: '010-1234-5678',
    question: [
      '병원 주소: 서울시 강남구 테헤란로 1',
      '원장님 성함: 홍길동',
      '병원 홈페이지: https://clinic.example.com',
    ].join('\n'),
  }), {
    address: '서울시 강남구 테헤란로 1',
    directorName: '홍길동',
    homepage: 'https://clinic.example.com',
    contact: '010-1234-5678',
  })
})

test('inquiry parser accepts short labels and leaves missing values explicit', () => {
  assert.deepEqual(readInquiryDetails({
    contact: ' director@example.com ',
    question: '주소： 부산시 해운대구\n원장명: 김원장',
  }), {
    address: '부산시 해운대구',
    directorName: '김원장',
    homepage: null,
    contact: 'director@example.com',
  })
})

test('only the five-field shape replaces legacy free-form inquiry copy', () => {
  assert.equal(hasStructuredInquiryDetails({
    clinic_type: '도입문의',
    source: 'INQUIRY',
    question: '형식이 일부 유실된 문의',
  }), true)
  assert.equal(hasStructuredInquiryDetails({
    clinic_type: '내과',
    source: 'INQUIRY',
    question: '기존 자유 문의',
  }), false)
  assert.equal(hasStructuredInquiryDetails({
    clinic_type: '내과',
    source: 'INQUIRY',
    question: '병원 주소: 서울시 강남구',
  }), true)
})
