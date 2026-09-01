export interface HospitalActivationInput {
  profile_complete?: boolean | null
  v0_report_done?: boolean | null
  site_built?: boolean | null
}

export interface ActivationPrerequisite {
  key: keyof HospitalActivationInput
  label: string
  hrefSuffix: 'profile' | 'reports'
}

export interface ServerActivationPrerequisite {
  key: string
  label: string
  action: string
  passed: boolean
}

export interface ServerActivationBlockerResponse {
  code?: string
  missing?: string[]
  prerequisites?: ServerActivationPrerequisite[]
}

export interface ServerActivationBlocker {
  key: string
  label: string
  action: string
}

const DISPLAY_PREREQUISITES: readonly ActivationPrerequisite[] = [
  { key: 'profile_complete', label: '병원 기본 정보 완료', hrefSuffix: 'profile' },
  { key: 'v0_report_done', label: '초기 진단 리포트 생성', hrefSuffix: 'reports' },
  { key: 'site_built', label: '콘텐츠 허브 준비', hrefSuffix: 'profile' },
]

const SERVER_GATE_ORDER = [
  'profile_complete',
  'v0_report_done',
  'site_built',
] as const

/**
 * Domain UI용 선행 단계 미리보기다. ACTIVE 권한 판정은 서버 응답만이 가진다.
 */
export function missingActivationPrerequisites(hospital: HospitalActivationInput): ActivationPrerequisite[] {
  return DISPLAY_PREREQUISITES.filter((item) => !hospital[item.key])
}

/** 서버가 반환한 blocker의 문구와 판정을 보존하고 화면 순서만 정규화한다. */
export function readServerActivationBlockers(
  response: ServerActivationBlockerResponse,
): ServerActivationBlocker[] {
  const missing = new Set(response.missing ?? [])
  const byKey = new Map(
    (response.prerequisites ?? [])
      .filter((item) => !item.passed && missing.has(item.key))
      .map((item) => [item.key, item]),
  )

  return SERVER_GATE_ORDER.flatMap((key) => {
    const blocker = byKey.get(key)
    return blocker ? [{ key: blocker.key, label: blocker.label, action: blocker.action }] : []
  })
}

export function isPlatformAddressBrowsable(hospital: { site_live?: boolean | null }): boolean {
  return hospital.site_live === true
}

export interface PlatformActivationInput extends HospitalActivationInput {
  site_live?: boolean | null
  aeo_domain?: string | null
  status?: string | null
}

/**
 * 기본 플랫폼 주소가 어떤 경로로 운영을 시작하는가.
 *
 * - `live`: 이미 공개 운영 중 — 사람이 할 일 없음
 * - `automatic`: 선행 조건이 모두 통과했고 자기 도메인이 없다 — 허브 준비 태스크가
 *   그대로 ACTIVE로 전환한다(백엔드 `services/hospital_activation.py`). 버튼을 두면
 *   AE가 이미 끝난 일을 누르게 된다.
 * - `manual`: 자기 도메인이 지정됐거나 일시 정지 상태 — DNS는 병원 것이라 시점을
 *   시스템이 정할 수 없다. 버튼을 남긴다.
 * - `blocked`: 선행 조건이 남아 있다.
 */
export type PlatformActivationMode = 'live' | 'automatic' | 'manual' | 'blocked'

export function hasCustomDomain(hospital: PlatformActivationInput): boolean {
  return (hospital.aeo_domain ?? '').trim().length > 0
}

export function platformActivationMode(hospital: PlatformActivationInput): PlatformActivationMode {
  if (isPlatformAddressBrowsable(hospital)) return 'live'
  if (missingActivationPrerequisites(hospital).length > 0) return 'blocked'
  if (hasCustomDomain(hospital) || hospital.status === 'PAUSED') return 'manual'
  return 'automatic'
}
