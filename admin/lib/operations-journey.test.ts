import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  RECOVERY_ADAPTERS,
  developerSupportText,
  isExpectedClipboardFailure,
  isExpectedOperatorRequestFailure,
  leadSourceLabel,
  operatorIssue,
  operatorIssueText,
  safeOperatorError,
} from './operations-journey.ts'

test('clipboard failures distinguish expected browser denial from unknown faults', () => {
  assert.equal(isExpectedClipboardFailure(new DOMException('denied', 'NotAllowedError')), true)
  assert.equal(isExpectedClipboardFailure(new TypeError('clipboard unavailable')), true)
  assert.equal(isExpectedClipboardFailure(new Error('unexpected application fault')), false)
  assert.equal(isExpectedClipboardFailure('raw failure'), false)
})

test('request failures distinguish expected transport responses from unknown faults', () => {
  assert.equal(isExpectedOperatorRequestFailure(new TypeError('network unavailable')), true)
  assert.equal(isExpectedOperatorRequestFailure(new DOMException('aborted', 'AbortError')), true)
  assert.equal(isExpectedOperatorRequestFailure(new Error('unexpected application fault')), false)
})

test('lead intake paths use plain labels and fail closed for unknown values', () => {
  assert.equal(leadSourceLabel('/ops-qa'), '운영 점검')
  assert.equal(leadSourceLabel('/ai-diagnosis'), '무료 진단 신청')
  assert.equal(leadSourceLabel('/unknown/internal/path'), '접수 경로 확인 필요')
})

test('every surface error explains the problem, customer impact, and exact action', () => {
  const issue = operatorIssue('operations', '운영 목록 다시 불러오기를 누르세요.')
  const text = operatorIssueText(issue)

  assert.match(text, /^문제:/)
  assert.match(text, /고객 영향:/)
  assert.match(text, /지금 할 일: 운영 목록 다시 불러오기를 누르세요/)
  assert.doesNotMatch(text, /Celery|Redis|SLA|manifest|UNKNOWN|FAILED/)
})

test('generic exceptions are never reflected into marketer copy', () => {
  const raw = new Error('ECONNREFUSED redis://secret-host:6379')
  const copy = safeOperatorError('leads', '상담 요청 다시 불러오기를 누르세요.')

  assert.doesNotMatch(copy, new RegExp(raw.message))
  assert.match(copy, /상담 요청 다시 불러오기/)
})

test('developer fallback contains safe screen context without a raw exception', () => {
  const issue = safeOperatorError('content', '콘텐츠 목록 다시 불러오기를 누르세요.')
  const copy = developerSupportText('content', issue, 'http://localhost:3000/hospitals/h1/content')

  assert.match(copy, /개발팀 문의용 정보/)
  assert.match(copy, /화면 주소:/)
  assert.doesNotMatch(copy, /payload|traceback|task_id/)
})

test('the runbook adapter matrix has one real Admin route and action per supported recovery', () => {
  assert.equal(RECOVERY_ADAPTERS.length, 8)
  assert.equal(new Set(RECOVERY_ADAPTERS.map((item) => item.id)).size, 8)
  for (const adapter of RECOVERY_ADAPTERS) {
    assert.match(adapter.route, /^\/(operations|hospitals|leads)/)
    assert.ok(adapter.action.length >= 3)
  }
})

test('live marketer surfaces fail closed instead of rendering raw states or exceptions', () => {
  const leads = readFileSync(new URL('../app/leads/page.tsx', import.meta.url), 'utf8')
  const content = readFileSync(new URL('../app/hospitals/[id]/content/page.tsx', import.meta.url), 'utf8')
  const onboarding = readFileSync(new URL('../app/hospitals/[id]/onboarding/page.tsx', import.meta.url), 'utf8')
  const globalError = readFileSync(new URL('../app/error.tsx', import.meta.url), 'utf8')
  const shell = readFileSync(new URL('../app/AdminShell.tsx', import.meta.url), 'utf8')
  const hospitalLayout = readFileSync(new URL('../app/hospitals/[id]/layout.tsx', import.meta.url), 'utf8')
  const operations = readFileSync(new URL('../app/operations/page.tsx', import.meta.url), 'utf8')
  const currentAction = readFileSync(new URL('../app/operations/CurrentActionStrip.tsx', import.meta.url), 'utf8')
  const hospitalNew = readFileSync(new URL('../app/hospitals/new/page.tsx', import.meta.url), 'utf8')
  const hospitals = readFileSync(new URL('../app/hospitals/page.tsx', import.meta.url), 'utf8')
  const profile = readFileSync(new URL('../app/hospitals/[id]/profile/page.tsx', import.meta.url), 'utf8')
  const schedule = readFileSync(new URL('../app/hospitals/[id]/schedule/page.tsx', import.meta.url), 'utf8')
  const queryTargets = readFileSync(new URL('../app/hospitals/[id]/query-targets/page.tsx', import.meta.url), 'utf8')
  const dashboard = readFileSync(new URL('../app/hospitals/[id]/dashboard/page.tsx', import.meta.url), 'utf8')

  assert.match(leads, /createPortal\(\(/)
  assert.doesNotMatch(leads, /\{lead\.notification_error/)
  assert.match(leads, /고객 영향: 신청자가 정확한 진단 리포트를 받지 못합니다/)
  assert.match(leads, /candidatesLoading \|\| Boolean\(candidatesError\)/)
  assert.match(leads, /중복 확인 다시 시도/)
  assert.match(leads, /병원 목록에서 확인/)
  assert.doesNotMatch(leads, /1분 안에/)
  assert.doesNotMatch(content, /\?\? item\.content_type\s*$/m)
  assert.match(content, /콘텐츠 유형 확인 필요/)
  assert.doesNotMatch(content, /Slack 알림 재시도/)
  assert.match(content, /Slack 알림 확인 필요/)
  assert.match(content, /알림 상태 확인/)
  assert.match(content, /min-w-\[1080px\]/)
  assert.match(content, /whitespace-nowrap break-keep/)
  assert.match(content, /min-h-11[\s\S]{0,180}전체 보기/)
  assert.match(content, /min-h-11[\s\S]{0,220}지금 발행/)
  assert.match(content, /min-h-11[\s\S]{0,220}즉시 재생성/)
  assert.match(content, /setError\(null\)[\s\S]{0,240}isExpectedOperatorRequestFailure/)
  assert.doesNotMatch(onboarding, /\?\? s\.(?:source_type|status)/)
  assert.doesNotMatch(onboarding, /mime_type \?\? 'binary'|ACTIVE 전환|\/site 노출/)
  assert.doesNotMatch(onboarding, /백엔드 검증|프로파일 URL 자료 후보|프로파일 화면/)
  assert.match(onboarding, /min-h-11[\s\S]{0,240}제외/)
  assert.doesNotMatch(globalError, /error\.message/)
  assert.match(globalError, /개발팀 문의용 정보 복사|OperatorIssuePanel/)
  assert.match(globalError, /safeOperatorError\('admin'/)
  assert.match(safeOperatorError('admin', '운영 화면 다시 불러오기를 누르세요.'), /문제: 운영 화면을 불러오지 못했습니다/)
  assert.doesNotMatch(`${shell}\n${leads}\n${content}\n${operations}`, /상담 리드|운영 관제|후행 점검|전체 파이프라인 상태|Research Preview|내부 운영 콘솔/)
  assert.match(currentAction, /원인과 처리 방법 보기/)
  assert.doesNotMatch(hospitalLayout, /label: hospital\.status/)
  assert.match(hospitalLayout, /상태 확인 필요/)
  assert.match(hospitalLayout, /label: '온보딩', path: 'onboarding', hint: '병원 자료 입력과 운영 기준 자동 준비'/)
  assert.match(shell, /break-keep text-pretty \[overflow-wrap:anywhere\]/)
  assert.match(leads, /font-semibold whitespace-nowrap text-slate-900/)
  assert.match(leads, /min-h-11 items-center justify-center whitespace-nowrap[\s\S]{0,500}온보딩 허브/)
  assert.match(leads, /inline-flex min-h-11 items-center[\s\S]{0,160}운영센터에서 상세 확인/)
  assert.match(leads, /inline-flex min-h-11 items-center[\s\S]{0,160}리포트 재발송/)
  assert.match(leads, /inline-flex min-h-11 items-center[\s\S]{0,160}1회 제한 해제/)
  assert.doesNotMatch(hospitalLayout, /hospital\?\.slug|\{hospital\.slug\}/)
  assert.doesNotMatch(hospitalLayout, /운영중/)
  // 공개 주소는 자기 도메인이든 기본 플랫폼 주소든 실제 값으로 말한다. 예전에는
  // 자기 도메인이 없으면 무조건 "준비 중"이라, 이미 서비스 중인 4곳이 미완성으로
  // 보였다(O-7). 원시 slug를 그대로 흘리지 않는다는 계약은 위 doesNotMatch가 지킨다.
  assert.doesNotMatch(hospitalLayout, /공개 주소 준비 중/)
  assert.match(hospitalLayout, /readHospitalDomainStatus\(hospital\)\.detail/)
  assert.doesNotMatch(leads, /\{candidate\.slug\}/)
  assert.match(leads, /불러온 상담 요청/)
  assert.doesNotMatch(leads, /\{lead\.source_path/)
  assert.doesNotMatch(`${shell}\n${readFileSync(new URL('../app/hospitals/[id]/DomainSetupPanel.tsx', import.meta.url), 'utf8')}`, /catch\s*\{/)
  assert.doesNotMatch(
    `${hospitalNew}\n${hospitals}\n${profile}\n${schedule}\n${queryTargets}\n${dashboard}`,
    /instanceof Error\s*\?[^:\n]*\.message|set[A-Za-z]*Error\([^\n]*(?:reason|err|error|cause|reloadError)\.message/,
  )
  assert.doesNotMatch(
    `${hospitalNew}\n${hospitals}\n${profile}\n${queryTargets}\n${dashboard}`,
    /상담 리드|프로파일 온보딩|프로파일 완료로 표시|운영중|DNS 대기|도메인 대기|V0 진단 리포트/,
  )

  const reportList = readFileSync(new URL('../app/hospitals/[id]/reports/ReportList.tsx', import.meta.url), 'utf8')
  const reportRuns = readFileSync(new URL('../app/hospitals/[id]/reports/ReportRunStatus.tsx', import.meta.url), 'utf8')
  // 고객 영향은 리포트 종류에 따라 다르다 — 월간은 전달 파이프라인이, 초기 진단은
  // 원장 보고 자료 자체가 막힌다. 두 문장 모두 운영자가 읽을 말로 남아 있어야 한다.
  assert.match(reportList, /고객 영향:/)
  assert.match(reportList, /최신 월간 보고 자료를 원장에게 전달할 수 없습니다/)
  assert.match(reportList, /초기 진단 결과를 원장에게 보고할 자료가 없습니다/)
  assert.match(reportRuns, /고객 영향: 생성·복구 진행 상태를 확인할 수 없습니다/)
  assert.match(reportRuns, /반복 실패 시 ‘개발팀 문의용 정보 복사’를 전달하세요/)
})
