export interface HospitalActivationInput {
  profile_complete?: boolean | null
  v0_report_done?: boolean | null
  site_built?: boolean | null
  schedule_set?: boolean | null
}

export interface ActivationPrerequisite {
  key: keyof HospitalActivationInput
  label: string
  hrefSuffix: 'profile' | 'reports' | 'schedule'
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
  { key: 'schedule_set', label: '콘텐츠 스케줄 설정', hrefSuffix: 'schedule' },
]

const SERVER_GATE_ORDER = [
  'handoff_accepted',
  'profile_complete',
  'v0_report_done',
  'site_built',
  'schedule_set',
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
