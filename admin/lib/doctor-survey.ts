import { ADMIN_COPY } from './admin-copy.ts'
import { formatDateTime } from './format.ts'

/** `GET /admin/hospitals/{id}/doctor-survey` 응답. */
export interface DoctorSurveyStatus {
  hospital_id: string
  status: 'NOT_ISSUED' | 'NOT_OPENED' | 'IN_PROGRESS' | 'SUBMITTED'
  link_url: string | null
  link_issued_at?: string | null
  new_clinic?: boolean
  respondent?: string | null
  last_submitted_at?: string | null
  submission_count?: number
  has_unsubmitted_changes?: boolean
  progress?: { answered: number; total: number }
  files?: DoctorSurveyFile[]
  photo_consent?: boolean
}

export interface DoctorSurveyFile {
  id: string
  kind: 'PHOTO' | 'LOGO' | 'CERTIFICATE'
  name: string
  mime: string
  size: number
  context: string | null
  url: string
}

export function doctorSurveyFileKindLabel(kind: DoctorSurveyFile['kind']): string {
  const copy = ADMIN_COPY.doctorSurvey
  if (kind === 'LOGO') return copy.fileKindLogo
  if (kind === 'CERTIFICATE') return copy.fileKindCertificate
  return copy.fileKindPhoto
}

/** 카드에 보이는 진행 상태 한 줄(제출 뒤 고친 답 안내는 따로). */
export function doctorSurveyStatusText(survey: DoctorSurveyStatus): string {
  const copy = ADMIN_COPY.doctorSurvey
  if (survey.status === 'SUBMITTED') {
    return copy.statusSubmitted.replace('{date}', formatDateTime(survey.last_submitted_at ?? null))
  }
  if (survey.status === 'IN_PROGRESS') {
    return copy.statusInProgress
      .replace('{answered}', String(survey.progress?.answered ?? 0))
      .replace('{total}', String(survey.progress?.total ?? 0))
  }
  if (survey.status === 'NOT_OPENED') return copy.statusNotOpened
  return ''
}

/** `GET /admin/hospitals/{id}/doctor-survey/facts`의 한 항목. */
export interface DoctorSurveyFact {
  field: string
  label: string
  value: string
  /** 반영하면 저장되는 하위 내용(약력·자격·진료 설명·요일별 시간). */
  details: string[]
  current: string
  applicable: boolean
  patch: Record<string, unknown> | null
  reason: string | null
}

/** 반영할 항목 이름 — 서버가 잠금 아래서 다시 확인하고 저장한다. 반영할 수 없는 항목은 보내지 않는다. */
export function chosenFactFields(facts: DoctorSurveyFact[], selected: ReadonlySet<string>): string[] {
  return facts.filter((fact) => fact.applicable && selected.has(fact.field)).map((fact) => fact.field)
}
