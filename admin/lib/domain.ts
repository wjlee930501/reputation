// 커스텀 도메인 저장/검증 오류 해석 헬퍼.
// backend 계약:
//   - 도메인 저장: 422 → 잘못된 호스트네임 (한국어 메시지), 409 → 다른 병원이 이미 사용 중
//   - 도메인 검증(POST /domain/verify): 409 → DNS는 정상이나 운영 시작 전 선행 단계 미완료 (detail에 단계 목록)
import { ApiError } from './api.ts'

export type DomainErrorKind = 'invalid' | 'conflict' | 'prerequisite' | 'generic'
export type DomainManagementMode = 'HOSPITAL_MANAGED' | 'MOTIONLABS_MANAGED'
export type DomainDnsStrategy = 'CNAME' | 'APEX_ADDRESS'

export interface DomainSetupRecord {
  type: 'CNAME' | 'A' | 'AAAA'
  name: string
  value: string
  ttl: string
  purpose: string
}

export interface DomainSetupChecklistItem {
  key: string
  label: string
  description: string
  status: 'DONE' | 'PENDING' | 'BLOCKED'
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

export function buildFallbackDomainSetupPlan(domain: string, expectedCname: string): DomainSetupPlan {
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
        value: expectedCname,
        ttl: '300',
        purpose: '병원 정보 허브 트래픽을 Reputation 플랫폼으로 연결',
      },
    ],
    checklist: [
      {
        key: 'domain_saved',
        label: '도메인 저장',
        description: '병원 계정에 연결할 도메인을 저장합니다.',
        status: domain ? 'DONE' : 'PENDING',
      },
      {
        key: 'dns_record',
        label: 'DNS 레코드 등록',
        description: '등록기관 DNS 관리 화면에 안내된 레코드를 추가합니다.',
        status: 'PENDING',
      },
      {
        key: 'dns_verified',
        label: 'DNS 검증',
        description: 'DNS 전파 후 연결 검증을 실행합니다.',
        status: 'PENDING',
      },
      {
        key: 'certificate_ready',
        label: 'HTTPS 인증서',
        description: '인증서가 발급되면 병원 정보 허브가 HTTPS로 제공됩니다.',
        status: 'PENDING',
      },
    ],
    warnings: [],
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
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
