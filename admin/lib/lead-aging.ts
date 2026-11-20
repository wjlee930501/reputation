/**
 * 상담 요청의 경과 시간.
 *
 * 목록은 접수 시각을 절대 시각(`2026. 08. 20. 14:32`)으로만 보여줬다. 그래서 어느
 * 요청이 오래 기다렸는지 보려면 AE가 매 줄마다 지금 시각과 머릿속으로 뺄셈을 해야 했다.
 *
 * 첫 연락 기한(24시간) 표시와 기한 순 정렬은 2026-11-20 마케팅 요청으로 걷어 냈다
 * (삭제 확정) — 실제 연락 여부를 기록하는 곳이 없어, 연락을 마친 요청도 「기한 초과」로
 * 붉게 남았다. 백엔드 「확인 필요」 필터(신규 또는 미연락)는 그대로다.
 */

export interface LeadAgingRow {
  created_at?: string | null
}

export interface LeadAging {
  /** 접수 후 경과 시간(시간 단위). 알 수 없으면 null */
  elapsedHours: number | null
  /** "3시간 전 접수" 같은 상대 표기 */
  elapsedLabel: string
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
    return { elapsedHours: null, elapsedLabel: '접수 시각 확인 필요' }
  }
  const elapsedHours = Math.max(0, (now - createdAt) / 3_600_000)
  return { elapsedHours, elapsedLabel: formatElapsed(elapsedHours) }
}
