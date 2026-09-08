import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const NEW_HOSPITAL_PAGE = readFileSync(
  new URL('../app/hospitals/new/page.tsx', import.meta.url),
  'utf8',
)
const ADMIN_SHELL = readFileSync(
  new URL('../app/AdminShell.tsx', import.meta.url),
  'utf8',
)

test('계약 등록 화면은 SLA 대신 운영자가 지킬 수 있는 기한을 말한다', () => {
  assert.doesNotMatch(NEW_HOSPITAL_PAGE, /\bSLA\b/)
  assert.match(NEW_HOSPITAL_PAGE, /인수 처리 기한/)
})

test('계약 등록 화면은 사이드바 이름과 하는 일을 함께 설명한다', () => {
  assert.match(ADMIN_SHELL, /label: '계약 등록'/)
  assert.match(NEW_HOSPITAL_PAGE, /병원을 생성하고 고객 인수를 승인한 뒤 병원 운영 화면으로 이동합니다\./)
  assert.doesNotMatch(
    ADMIN_SHELL,
    /병원 자료, 운영 기준 자동 준비, 콘텐츠 자동 발행·공개 내용 확인, 월간 리포트 순서/,
  )
})
