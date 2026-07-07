// 병원 일시정지/재개 전환 규칙.
//
// 백엔드 계약:
//   POST /hospitals/{id}/pause  (ACTIVE|PENDING_DOMAIN → PAUSED)
//   POST /hospitals/{id}/resume (PAUSED → ACTIVE 또는 PENDING_DOMAIN)
// 둘 다 병원 상세를 그대로 반환한다.
export type HospitalLifecycleAction = 'pause' | 'resume'

const PAUSABLE_STATUSES: readonly string[] = ['ACTIVE', 'PENDING_DOMAIN']

/** 현재 상태에서 AE가 취할 수 있는 전환 액션(있다면)을 반환한다. */
export function getHospitalLifecycleAction(status: string | null | undefined): HospitalLifecycleAction | null {
  if (!status) return null
  if (PAUSABLE_STATUSES.includes(status)) return 'pause'
  if (status === 'PAUSED') return 'resume'
  return null
}

export function hospitalLifecycleConfirmMessage(action: HospitalLifecycleAction): string {
  return action === 'pause'
    ? '일시정지하면 콘텐츠 자동 생성·측정이 중단됩니다. 계속하시겠습니까?'
    : '재개하면 콘텐츠 자동 생성·측정이 다시 시작됩니다. 계속하시겠습니까?'
}

export function hospitalLifecycleActionPath(hospitalId: string, action: HospitalLifecycleAction): string {
  return `/admin/hospitals/${hospitalId}/${action}`
}
