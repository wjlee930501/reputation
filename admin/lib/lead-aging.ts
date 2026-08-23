/**
 * 상담 요청의 경과 시간과 첫 연락 기한.
 *
 * 목록은 접수 시각을 절대 시각(`2026. 08. 20. 14:32`)으로만 보여줬다. 그래서 어느
 * 요청이 오래 방치됐는지 보려면 AE가 매 줄마다 지금 시각과 머릿속으로 뺄셈을 해야 했고,
 * 목록이 접수 시각 내림차순이라 **가장 오래 기다린 요청이 화면 맨 아래**에 있었다.
 *
 * 첫 연락 목표는 접수 후 24시간이다. 전환·보류된 요청은 대상이 아니다 — 이미 처리가
 * 끝난 요청에 기한을 붙이면 없는 밀린 일을 만든다.
 */

/** 첫 연락 목표 시간 — 계약 인수 SLA와 별개인, 상담 요청 응답 기준. */
export const LEAD_FIRST_CONTACT_TARGET_HOURS = 24

export interface LeadAgingRow {
  created_at?: string | null
  converted_at?: string | null
  converted_hospital_id?: string | null
  status?: string | null
}

export type LeadSlaState =
  /** 기한 안 */
  | 'OK'
  /** 기한이 임박했다 (남은 시간 6시간 이내) */
  | 'DUE_SOON'
  /** 기한을 넘겼다 */
  | 'OVERDUE'
  /** 이미 전환·보류돼 기한 대상이 아니다 */
  | 'CLOSED'
  /** 접수 시각을 읽을 수 없다 */
  | 'UNKNOWN'

export interface LeadAging {
  /** 접수 후 경과 시간(시간 단위). 알 수 없으면 null */
  elapsedHours: number | null
  /** "3시간 전" 같은 상대 표기 */
  elapsedLabel: string
  slaState: LeadSlaState
  slaLabel: string
}

function formatElapsed(hours: number): string {
  if (hours < 1) {
    const minutes = Math.max(1, Math.floor(hours * 60))
    return `${minutes}분 전 접수`
  }
  if (hours < 24) return `${Math.floor(hours)}시간 전 접수`
  const days = Math.floor(hours / 24)
  return `${days}일 전 접수`
}

export function describeLeadAging(lead: LeadAgingRow, now: number): LeadAging {
  const createdAt = lead.created_at ? Date.parse(lead.created_at) : Number.NaN
  if (!Number.isFinite(createdAt)) {
    return {
      elapsedHours: null,
      elapsedLabel: '접수 시각 확인 필요',
      slaState: 'UNKNOWN',
      slaLabel: '기한 산출 불가',
    }
  }

  const elapsedHours = Math.max(0, (now - createdAt) / 3_600_000)
  const elapsedLabel = formatElapsed(elapsedHours)
  const isClosed =
    Boolean(lead.converted_hospital_id)
    || Boolean(lead.converted_at)
    || lead.status === 'CONVERTED'
    || lead.status === 'DISMISSED'

  if (isClosed) {
    return { elapsedHours, elapsedLabel, slaState: 'CLOSED', slaLabel: '처리 완료' }
  }

  const remainingHours = LEAD_FIRST_CONTACT_TARGET_HOURS - elapsedHours
  if (remainingHours <= 0) {
    const overdue = Math.floor(-remainingHours)
    return {
      elapsedHours,
      elapsedLabel,
      slaState: 'OVERDUE',
      slaLabel: overdue >= 24 ? `첫 연락 기한 ${Math.floor(overdue / 24)}일 초과` : `첫 연락 기한 ${overdue}시간 초과`,
    }
  }
  if (remainingHours <= 6) {
    return {
      elapsedHours,
      elapsedLabel,
      slaState: 'DUE_SOON',
      slaLabel: `첫 연락 기한 ${Math.ceil(remainingHours)}시간 남음`,
    }
  }
  return {
    elapsedHours,
    elapsedLabel,
    slaState: 'OK',
    slaLabel: `첫 연락 기한 ${Math.ceil(remainingHours)}시간 남음`,
  }
}

/**
 * 기한을 넘긴 요청을 위로 올린다.
 *
 * 접수 시각 내림차순만 쓰면 가장 오래 기다린 요청이 맨 아래에 남는다. 서버 정렬은
 * 그대로 두고, 화면에서 초과 → 임박 → 나머지 순으로만 끌어올린다(같은 등급 안에서는
 * 서버가 준 순서를 유지한다).
 */
export function sortLeadsByAttention<T extends LeadAgingRow>(leads: T[], now: number): T[] {
  const rank: Record<LeadSlaState, number> = {
    OVERDUE: 0,
    DUE_SOON: 1,
    OK: 2,
    UNKNOWN: 3,
    CLOSED: 4,
  }
  return (Array.isArray(leads) ? [...leads] : [])
    .map((lead, index) => ({ lead, index, state: describeLeadAging(lead, now).slaState }))
    .sort((a, b) => rank[a.state] - rank[b.state] || a.index - b.index)
    .map((entry) => entry.lead)
}
