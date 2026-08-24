import type { Hospital } from '@/types'

export type HospitalStatusFilter = 'all' | 'active' | 'onboarding'

const ONBOARDING_STATUSES = new Set<Hospital['status']>([
  'ONBOARDING',
  'ANALYZING',
  'BUILDING',
  'PENDING_DOMAIN',
])

export function isOnboardingHospital(hospital: Hospital): boolean {
  return ONBOARDING_STATUSES.has(hospital.status)
}

export function hospitalMatchesStatus(
  hospital: Hospital,
  filter: HospitalStatusFilter,
): boolean {
  if (filter === 'active') return hospital.status === 'ACTIVE'
  if (filter === 'onboarding') return isOnboardingHospital(hospital)
  return true
}

export function hospitalStatusCounts(hospitals: Hospital[]) {
  return {
    total: hospitals.length,
    active: hospitals.filter((hospital) => hospitalMatchesStatus(hospital, 'active')).length,
    onboarding: hospitals.filter((hospital) => hospitalMatchesStatus(hospital, 'onboarding')).length,
  }
}
