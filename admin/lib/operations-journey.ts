import { ApiError } from './api.ts'

export type OperatorSurface = 'leads' | 'onboarding' | 'content' | 'operations' | 'session' | 'admin'

export interface OperatorIssue {
  readonly problem: string
  readonly customerImpact: string
  readonly nextAction: string
}

const SURFACE_COPY: Record<OperatorSurface, Omit<OperatorIssue, 'nextAction'>> = {
  leads: {
    problem: '상담 요청과 무료 진단 기록을 불러오거나 변경하지 못했습니다.',
    customerImpact: '고객 인수와 진단 후속 처리가 늦어질 수 있습니다.',
  },
  onboarding: {
    problem: '병원 온보딩 자료와 현재 진행 상태를 확인하지 못했습니다.',
    customerImpact: '잘못된 순서로 다음 단계를 진행할 수 있어 자동 운영을 시작하지 않습니다.',
  },
  content: {
    problem: '콘텐츠의 최신 상태를 확인하거나 요청한 변경을 완료하지 못했습니다.',
    customerImpact: '확인되지 않은 글은 공개하거나 완료로 기록하지 않습니다.',
  },
  operations: {
    problem: '운영 센터의 최신 처리 목록을 확인하지 못했습니다.',
    customerImpact: '현재 화면만으로 고객 작업의 성공이나 실패를 판단할 수 없습니다.',
  },
  session: {
    problem: '현재 운영자 계정의 로그아웃을 완료하지 못했습니다.',
    customerImpact: '공용 기기라면 현재 계정 화면이 계속 열려 있을 수 있습니다.',
  },
  admin: {
    problem: '운영 화면을 불러오지 못했습니다.',
    customerImpact: '최신 고객 상태를 확인할 수 없어 이 화면에서 다음 작업을 진행하지 않습니다.',
  },
}

export function operatorIssue(surface: OperatorSurface, nextAction: string): OperatorIssue {
  return { ...SURFACE_COPY[surface], nextAction }
}

export function operatorIssueText(issue: OperatorIssue): string {
  return [
    `문제: ${issue.problem}`,
    `고객 영향: ${issue.customerImpact}`,
    `지금 할 일: ${issue.nextAction}`,
  ].join('\n')
}

export function safeOperatorError(surface: OperatorSurface, nextAction: string): string {
  return operatorIssueText(operatorIssue(surface, nextAction))
}

export function developerSupportText(
  surface: OperatorSurface,
  issueText: string,
  location: string,
): string {
  return [
    '[개발팀 문의용 정보]',
    `화면: ${surface}`,
    issueText,
    `화면 주소: ${location}`,
  ].join('\n')
}

export function isExpectedClipboardFailure(
  error: unknown,
): error is DOMException | TypeError {
  return error instanceof DOMException || error instanceof TypeError
}

export function isExpectedOperatorRequestFailure(
  error: unknown,
): error is ApiError | DOMException | TypeError {
  return error instanceof ApiError || error instanceof DOMException || error instanceof TypeError
}

export function leadSourceLabel(sourcePath: string | null | undefined): string {
  if (sourcePath === '/ops-qa') return '운영 점검'
  if (sourcePath === '/ai-diagnosis') return '무료 진단 신청'
  if (sourcePath === '/' || sourcePath === '/contact') return '홈페이지 문의'
  return '접수 경로 확인 필요'
}

export const RECOVERY_ADAPTERS = [
  { id: 'content-generation', route: '/operations?queue=incidents', action: '작업 다시 시도' },
  { id: 'publish-notification', route: '/hospitals/:id/content', action: 'Slack 다시 보내기' },
  { id: 'lead-diagnosis', route: '/leads', action: '다시 측정 또는 리포트 다시 만들기' },
  { id: 'naver-single-url', route: '/hospitals/:id/onboarding', action: '다시 처리' },
  { id: 'monthly-report', route: '/hospitals/:id/reports', action: '리포트 다시 만들기' },
  { id: 'domain-health', route: '/operations?queue=incidents', action: '복구 결과 확인' },
  { id: 'cache-revalidation', route: '/operations?queue=incidents', action: '복구 결과 확인' },
  { id: 'worker-failure', route: '/operations?queue=incidents', action: '작업 다시 시도' },
] as const
