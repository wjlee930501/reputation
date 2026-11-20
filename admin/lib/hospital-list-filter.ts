import type { Hospital } from '@/types'

/**
 * 병원 목록 필터(2026-11-20 마케팅 요청): 기본은 운영 중 + 준비 중. 일시 정지 병원은 기본 목록에서
 * 빼고 아래 접힌 묶음으로 둔다. `reportGap`은 지난달 원장 보고 미생성·미전달 — 목록 위 상자와 같은
 * `/admin/operations/attention` 판정을 쓴다(한 가지 정의).
 */
export type HospitalStatusFilter = 'default' | 'all' | 'active' | 'onboarding' | 'paused' | 'reportGap'

/** 이 필터가 실제로 읽는 것은 상태 하나다 — 목록 행과 상세를 모두 그대로 받는다. */
type HospitalStatusRow = Pick<Hospital, 'status'> & { id?: string }

const ONBOARDING_STATUSES = new Set<Hospital['status']>([
  'ONBOARDING',
  'ANALYZING',
  'BUILDING',
  'PENDING_DOMAIN',
])

export function isOnboardingHospital(hospital: HospitalStatusRow): boolean {
  return ONBOARDING_STATUSES.has(hospital.status)
}

export function hospitalMatchesStatus(
  hospital: HospitalStatusRow,
  filter: HospitalStatusFilter,
  reportGapIds: ReadonlySet<string> = new Set(),
): boolean {
  if (filter === 'default') return hospital.status !== 'PAUSED'
  if (filter === 'active') return hospital.status === 'ACTIVE'
  if (filter === 'onboarding') return isOnboardingHospital(hospital)
  if (filter === 'paused') return hospital.status === 'PAUSED'
  if (filter === 'reportGap') return Boolean(hospital.id && reportGapIds.has(hospital.id))
  return true
}

export function hospitalStatusCounts(
  hospitals: HospitalStatusRow[],
  reportGapIds: ReadonlySet<string> = new Set(),
) {
  const count = (filter: HospitalStatusFilter) =>
    hospitals.filter((hospital) => hospitalMatchesStatus(hospital, filter, reportGapIds)).length
  return {
    total: hospitals.length,
    default: count('default'),
    active: count('active'),
    onboarding: count('onboarding'),
    paused: count('paused'),
    reportGap: count('reportGap'),
  }
}
