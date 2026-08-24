// 커스텀 도메인 저장/검증 오류 해석 헬퍼.
// backend 계약:
//   - 도메인 저장: 422 → 잘못된 호스트네임 (한국어 메시지), 409 → 다른 병원이 이미 사용 중
//   - 도메인 검증(POST /domain/verify): 409 → DNS는 정상이나 운영 시작 전 선행 단계 미완료 (detail에 단계 목록)
import { ApiError } from './api.ts'
import { isRecord } from './type-guards.ts'

export type DomainErrorKind = 'invalid' | 'conflict' | 'prerequisite' | 'generic'
export type DomainManagementMode = 'HOSPITAL_MANAGED' | 'MOTIONLABS_MANAGED'
export type DomainDnsStrategy = 'CNAME' | 'APEX_ADDRESS'

export interface DomainSetupRecord {
  type: 'CNAME' | 'A' | 'AAAA'
  name: string
  host: string
  registrar_host?: string | null
  value: string
  ttl: string
  purpose: string
}

export interface DomainSetupChecklistItem {
  key: string
  label: string
  description: string
  status: 'DONE' | 'PENDING' | 'WAITING' | 'BLOCKED'
}

export interface DomainSetupPlan {
  domain: string | null
  management_mode: DomainManagementMode
  dns_strategy: DomainDnsStrategy
  registrar: string | null
  dns_provider: string | null
  purchase_note: string | null
  expected_cname: string
  expected_addresses: string[]
  certificate_ready?: boolean
  certificate_phase?: string | null
  records: DomainSetupRecord[]
  checklist: DomainSetupChecklistItem[]
  warnings: string[]
}

export interface DomainErrorInfo {
  kind: DomainErrorKind
  message: string
  missingSteps: string[]
}

export function domainManagementModeLabel(mode: DomainManagementMode): string {
  switch (mode) {
    case 'HOSPITAL_MANAGED':
      return '병원 직접 관리'
    case 'MOTIONLABS_MANAGED':
      return 'MotionLabs 구매·관리'
  }
}

export function domainStrategyLabel(strategy: DomainDnsStrategy): string {
  switch (strategy) {
    case 'CNAME':
      return '서브도메인 CNAME'
    case 'APEX_ADDRESS':
      return '루트 도메인 A 레코드'
  }
}

/**
 * 도메인 연결 안내의 단계 순서 — 서버 응답과 이 fallback이 공유하는 정본.
 *
 * 이 목록의 순서가 화면에 붙는 번호를 정한다.
 */
export const DOMAIN_SETUP_STEP_ORDER = [
  {
    key: 'domain_saved',
    label: '도메인 저장',
    description: '병원 계정에 연결할 도메인을 저장합니다.',
  },
  {
    key: 'purchase',
    label: '구매/소유권 확인',
    description: '병원 또는 MotionLabs가 도메인 구매와 갱신 책임자를 확정합니다.',
  },
  {
    key: 'dns_record',
    label: 'DNS 레코드 등록 (운영자)',
    description: '등록기관 DNS 관리 화면에 안내된 레코드를 추가합니다.',
  },
  {
    key: 'dns_verified',
    label: 'DNS 검증 (운영자 작업 완료)',
    description: 'DNS 레코드 등록 후 연결 검증을 실행합니다. 검증 성공 시 온보딩 5단계 완료.',
  },
  {
    key: 'certificate_ready',
    label: 'HTTPS 인증서 (시스템 후속)',
    description: '인증서는 백그라운드에서 자동 발급됩니다.',
  },
] as const

export function buildFallbackDomainSetupPlan(domain: string, expectedCname: string): DomainSetupPlan {
  // DM-U2: CNAME 대상값에 trailing dot 포함 + FQDN 안내
  const cnameValueWithDot = expectedCname.endsWith('.') ? expectedCname : `${expectedCname}.`
  
  return {
    domain,
    management_mode: 'HOSPITAL_MANAGED',
    dns_strategy: 'CNAME',
    registrar: null,
    dns_provider: null,
    purchase_note: null,
    expected_cname: expectedCname,
    expected_addresses: [],
    records: [
      {
        type: 'CNAME',
        name: domain,
        host: domain,
        registrar_host: domain.split('.')[0] || null,
        value: cnameValueWithDot,
        ttl: '300 (또는 등록기관 최소값)',
        purpose: '병원 정보 허브 트래픽을 Reputation 플랫폼으로 연결',
      },
    ],
    // 번호는 라벨에 박지 않는다. 서버가 돌려주는 목록은 다섯 단계인데 이 fallback은
    // 네 단계였고, 서버 쪽에서는 'DNS 레코드 등록'만 번호가 없어서 안내를 번호대로
    // 따라가면 등록기관에서 해야 할 유일한 작업을 건너뛰게 됐다(E-3). 번호는 화면이
    // 순서에서 매기고, 두 목록은 같은 단계를 같은 순서로 갖는다.
    checklist: DOMAIN_SETUP_STEP_ORDER.map((step) => ({
      key: step.key,
      label: step.label,
      description: step.description,
      status: step.key === 'domain_saved' && domain ? 'DONE' : 'PENDING',
    })),
    warnings: [
      'FQDN 입력 시 끝에 점(.)을 붙이는 등록기관도 있습니다. 등록기관 UI 규칙을 확인하세요.',  // DM-U2
      'TTL은 DNS 검증 속도에 영향을 주지 않습니다. 등록기관 최소값을 사용하세요.',  // DM-U1
    ],
  }
}

function toStepLabel(entry: unknown): string {
  if (typeof entry === 'string') return entry
  if (isRecord(entry)) {
    if (typeof entry.label === 'string') return entry.label
    if (typeof entry.message === 'string') return entry.message
    if (typeof entry.title === 'string') return entry.title
  }
  return ''
}

function readStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.map(toStepLabel).filter(Boolean)
}

/** 409 detail에서 미완료 선행 단계 목록을 찾아낸다. 알려진 키 이름을 순서대로 시도. */
export function extractMissingSteps(detail: unknown): string[] {
  if (Array.isArray(detail)) return readStringList(detail)
  if (!isRecord(detail)) return []
  for (const key of ['missing', 'missing_steps', 'prerequisites', 'prerequisite_steps', 'steps', 'checklist']) {
    const list = readStringList(detail[key])
    if (list.length > 0) return list
  }
  return []
}

/**
 * detail이 구조화 목록 없이 "...단계가 남아 있습니다: V0 리포트, 콘텐츠 스케줄"처럼
 * 문자열 한 줄로 내려오는 경우, 콜론 뒤 항목을 체크리스트로 분해한다.
 */
export function parseStepsFromMessage(message: string): string[] {
  const match = /[:：]\s*([^:：]+)$/.exec(message)
  if (!match) return []
  return match[1]
    .split(/[,·]/)
    .map((step) => step.replace(/\.$/, '').trim())
    .filter(Boolean)
}

/**
 * 도메인 저장/검증 오류를 화면에서 구분 표시할 수 있는 형태로 변환한다.
 * - 422 → invalid (형식 오류, backend 한국어 메시지 그대로)
 * - 409 + 단계 목록 → prerequisite (검증 전 선행 단계 미완료)
 * - 409 (목록 없음) → conflict (다른 병원이 이미 사용 중)
 */
export function readDomainError(error: unknown, fallback: string): DomainErrorInfo {
  if (!(error instanceof ApiError)) {
    return {
      kind: 'generic',
      message: error instanceof Error ? error.message : fallback,
      missingSteps: [],
    }
  }
  if (error.status === 422) {
    return { kind: 'invalid', message: error.message, missingSteps: [] }
  }
  if (error.status === 409) {
    const missingSteps = extractMissingSteps(error.detail)
    if (missingSteps.length > 0) {
      return { kind: 'prerequisite', message: error.message, missingSteps }
    }
    return { kind: 'conflict', message: error.message, missingSteps: [] }
  }
  return { kind: 'generic', message: error.message, missingSteps: [] }
}
