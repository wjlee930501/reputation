export interface HospitalActivationInput {
  profile_complete?: boolean | null
  v0_report_done?: boolean | null
  site_built?: boolean | null
}

export interface ActivationPrerequisite {
  key: keyof HospitalActivationInput
  label: string
  hrefSuffix: 'profile' | 'reports' | 'schedule'
}

const PREREQUISITES: ActivationPrerequisite[] = [
  { key: 'profile_complete', label: '프로파일 완료', hrefSuffix: 'profile' },
  { key: 'v0_report_done', label: 'V0 리포트 생성', hrefSuffix: 'reports' },
  { key: 'site_built', label: '병원 정보 허브 빌드', hrefSuffix: 'profile' },
]

export function missingActivationPrerequisites(hospital: HospitalActivationInput): ActivationPrerequisite[] {
  return PREREQUISITES.filter((item) => !hospital[item.key])
}

export function canActivateHospital(hospital: HospitalActivationInput): boolean {
  return missingActivationPrerequisites(hospital).length === 0
}

export function isPlatformAddressBrowsable(hospital: { site_live?: boolean | null }): boolean {
  return hospital.site_live === true
}
