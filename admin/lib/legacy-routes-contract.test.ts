// 옛 병원 화면 8개는 redirect만 남는다 — 화면 본문이 다시 자라면 탭이 4개가 아니게 된다.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const LEGACY_SEGMENTS = [
  'dashboard',
  'onboarding',
  'profile',
  'schedule',
  'wiki',
  'essence',
  'query-targets',
  'exposure-actions',
]

const hospitalLayout = readFileSync(
  new URL('../app/hospitals/[id]/layout.tsx', import.meta.url),
  'utf8',
)
const adminShell = readFileSync(new URL('../app/AdminShell.tsx', import.meta.url), 'utf8')

test('옛 라우트 8개는 redirect 한 줄짜리 서버 화면이다', () => {
  for (const segment of LEGACY_SEGMENTS) {
    const source = readFileSync(
      new URL(`../app/hospitals/[id]/${segment}/page.tsx`, import.meta.url),
      'utf8',
    )

    assert.ok(source.split('\n').length <= 25, `${segment}: redirect 화면이 25줄을 넘었다`)
    assert.match(source, /import \{ redirect \} from 'next\/navigation'/, segment)
    assert.match(source, /legacyHospitalRedirect\(/, segment)
    assert.match(source, new RegExp(`'${segment}'`), segment)
    // 데이터를 읽으면 그건 화면이지 redirect가 아니다.
    assert.doesNotMatch(source, /fetchAPI\(/, segment)
    assert.doesNotMatch(source, /'use client'/, segment)
  }
})

test('병원 탭은 현황·병원 정보·콘텐츠·보고서 넷뿐이다', () => {
  const block = hospitalLayout.match(/const MAIN_TABS[\s\S]*?\n\]/)?.[0]
  assert.ok(block, 'MAIN_TABS 정의를 찾지 못했다')

  const paths = [...block.matchAll(/path: '([^']*)'/g)].map((match) => match[1])
  const labels = [...block.matchAll(/label: '([^']*)'/g)].map((match) => match[1])

  assert.deepEqual(paths, ['', 'info', 'content', 'reports'])
  assert.deepEqual(labels, ['현황', '병원 정보', '콘텐츠', '보고서'])
})

test('운영 설정 드롭다운과 옛 탭 이름은 남지 않는다', () => {
  assert.doesNotMatch(hospitalLayout, /CONFIG_TABS/)
  for (const gone of [
    '자료 모음',
    '운영 요약',
    '온보딩',
    '운영 설정',
    '병원 기본 정보',
    '발행 일정',
    '환자 질문',
    '노출 보완',
  ]) {
    assert.doesNotMatch(hospitalLayout, new RegExp(gone), gone)
  }
})

test('사이드바는 계약 등록이라고 부르고 주소는 그대로다', () => {
  assert.match(adminShell, /label: '계약 등록'/)
  assert.doesNotMatch(adminShell, /신규 병원 온보딩/)
  assert.match(adminShell, /href: '\/hospitals\/new'/)
})
