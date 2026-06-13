import assert from 'node:assert/strict'
import test from 'node:test'

import { ApiError } from './api.ts'
import {
  buildFallbackDomainSetupPlan,
  domainManagementModeLabel,
  domainStrategyLabel,
  extractMissingSteps,
  parseStepsFromMessage,
  readDomainError,
} from './domain.ts'

test('422 is read as an invalid-format error with the backend Korean message', () => {
  const error = new ApiError('올바른 도메인 형식이 아닙니다. 예: ai.clinicname.co.kr', 422)

  const info = readDomainError(error, '도메인 저장에 실패했습니다.')

  assert.equal(info.kind, 'invalid')
  assert.equal(info.message, '올바른 도메인 형식이 아닙니다. 예: ai.clinicname.co.kr')
  assert.deepEqual(info.missingSteps, [])
})

test('409 without a step list is read as an already-in-use conflict', () => {
  const error = new ApiError('이미 다른 병원에서 사용 중인 도메인입니다.', 409, {
    message: '이미 다른 병원에서 사용 중인 도메인입니다.',
  })

  const info = readDomainError(error, '도메인 저장에 실패했습니다.')

  assert.equal(info.kind, 'conflict')
  assert.deepEqual(info.missingSteps, [])
})

test('verify 409 with a prerequisite list is read as missing launch steps', () => {
  const error = new ApiError('운영 시작 전 완료해야 할 단계가 있습니다.', 409, {
    message: '운영 시작 전 완료해야 할 단계가 있습니다.',
    missing: ['프로파일 완료', 'V0 리포트 생성'],
  })

  const info = readDomainError(error, '도메인 검증에 실패했습니다.')

  assert.equal(info.kind, 'prerequisite')
  assert.deepEqual(info.missingSteps, ['프로파일 완료', 'V0 리포트 생성'])
})

test('extractMissingSteps reads known list keys and object entries with labels', () => {
  assert.deepEqual(extractMissingSteps({ prerequisites: ['콘텐츠 스케줄 설정'] }), ['콘텐츠 스케줄 설정'])
  assert.deepEqual(
    extractMissingSteps({ missing_steps: [{ label: '프로파일 완료' }, { message: 'V0 리포트 생성' }] }),
    ['프로파일 완료', 'V0 리포트 생성'],
  )
  assert.deepEqual(extractMissingSteps(['프로파일 완료']), ['프로파일 완료'])
  assert.deepEqual(extractMissingSteps('문자열 detail'), [])
  assert.deepEqual(extractMissingSteps(null), [])
})

test('parseStepsFromMessage splits a colon-suffixed Korean step list', () => {
  assert.deepEqual(
    parseStepsFromMessage('도메인 DNS는 확인됐지만 LIVE 전환 전 단계가 남아 있습니다: V0 리포트, 콘텐츠 스케줄'),
    ['V0 리포트', '콘텐츠 스케줄'],
  )
  assert.deepEqual(parseStepsFromMessage('콜론 없는 메시지'), [])
})

test('non-API errors fall back to a generic message', () => {
  const info = readDomainError(new Error('network down'), '도메인 검증에 실패했습니다.')
  assert.equal(info.kind, 'generic')
  assert.equal(info.message, 'network down')

  const unknown = readDomainError('oops', '도메인 검증에 실패했습니다.')
  assert.equal(unknown.kind, 'generic')
  assert.equal(unknown.message, '도메인 검증에 실패했습니다.')
})

test('domain setup labels distinguish hospital-owned and MotionLabs-managed flows', () => {
  assert.equal(domainManagementModeLabel('HOSPITAL_MANAGED'), '병원 직접 관리')
  assert.equal(domainManagementModeLabel('MOTIONLABS_MANAGED'), 'MotionLabs 구매·관리')
  assert.equal(domainStrategyLabel('CNAME'), '서브도메인 CNAME')
  assert.equal(domainStrategyLabel('APEX_ADDRESS'), '루트 도메인 A 레코드')
})

test('fallback setup plan gives operators a usable CNAME record before backend setup API responds', () => {
  const plan = buildFallbackDomainSetupPlan('ai.clinic.example', 'cname.reputation.motionlabs.kr')

  assert.equal(plan.domain, 'ai.clinic.example')
  assert.equal(plan.management_mode, 'HOSPITAL_MANAGED')
  assert.equal(plan.dns_strategy, 'CNAME')
  assert.deepEqual(plan.records, [
    {
      type: 'CNAME',
      name: 'ai.clinic.example',
      value: 'cname.reputation.motionlabs.kr',
      ttl: '300',
      purpose: '병원 정보 허브 트래픽을 Reputation 플랫폼으로 연결',
    },
  ])
  assert.deepEqual(
    plan.checklist.map((item) => item.key),
    ['domain_saved', 'dns_record', 'dns_verified', 'certificate_ready'],
  )
})
