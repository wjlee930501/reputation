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

test('계약 등록 화면은 운영자가 모르는 약어나 지킬 수 없는 기한을 말하지 않는다', () => {
  assert.doesNotMatch(NEW_HOSPITAL_PAGE, /\bSLA\b/)
  // 같은 요청에서 인수까지 끝나므로 인수 처리 기한 칸 자체가 없다.
  assert.doesNotMatch(NEW_HOSPITAL_PAGE, /인수 처리 기한/)
})

test('계약 등록 화면은 사이드바 이름과 하는 일을 함께 설명한다', () => {
  assert.match(ADMIN_SHELL, /label: '계약 등록'/)
  assert.match(NEW_HOSPITAL_PAGE, /병원을 만들고 계약을 기록하고 담당 AE 인수까지 한 번에 끝냅니다\./)
  assert.doesNotMatch(
    ADMIN_SHELL,
    /병원 자료, 운영 기준 자동 준비, 콘텐츠 자동 발행·공개 내용 확인, 월간 리포트 순서/,
  )
})
