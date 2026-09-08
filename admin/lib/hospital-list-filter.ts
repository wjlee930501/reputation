import type { Hospital } from '@/types'

export type HospitalStatusFilter = 'all' | 'active' | 'onboarding'

/** 이 필터가 실제로 읽는 것은 상태 하나다 — 목록 행과 상세를 모두 그대로 받는다. */
type HospitalStatusRow = Pick<Hospital, 'status'>

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
): boolean {
  if (filter === 'active') return hospital.status === 'ACTIVE'
  if (filter === 'onboarding') return isOnboardingHospital(hospital)
  return true
}

export function hospitalStatusCounts(hospitals: HospitalStatusRow[]) {
  return {
    total: hospitals.length,
    active: hospitals.filter((hospital) => hospitalMatchesStatus(hospital, 'active')).length,
    onboarding: hospitals.filter((hospital) => hospitalMatchesStatus(hospital, 'onboarding')).length,
  }
}
