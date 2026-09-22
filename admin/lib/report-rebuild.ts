import { reportMonthBlockReason, REPORT_MONTH_BLOCK_MESSAGE } from './report-period.ts'

/**
 * 보고서 목록 한 행에서 '다시 만들기'를 내줄지 판정한다.
 *
 * 지금까지 재생성은 「최근 월간 보고서 작업」 카드에서만, 그것도 실패·차단·중복 건너뜀
 * 상태에서만 가능했다. 정상적으로 만들어진 보고서를 생성 로직 개선 뒤 다시 만들 방법이
 * 없었고, 최근 3건 밖으로 밀린 과거 월은 아예 손이 닿지 않았다. 목록 행에서 같은 요청을
 * 보낼 수 있게 하되, 판정은 화면이 아니라 여기 한곳에서 한다.
 *
 * 전달 여부는 재생성을 막지 않는다 — 새 버전은 이전 보고서를 지우지 않고 버전을 올릴 뿐이라
 * 이미 보낸 파일이 바뀌지 않는다. 대신 "새 버전은 다시 전달해야 원장에게 닿는다"는 사실을
 * 경고로 남긴다. 막아 두면 AE는 개발팀을 거쳐야 하고, 경고 없이 열어 두면 새 버전을
 * 만들어 놓고 전달을 잊는다.
 */
export type ReportRebuildPlan =
  | { readonly kind: 'available'; readonly warning: string | null }
  | { readonly kind: 'unavailable'; readonly reason: string }

type RebuildContract = {
  readonly periodYear: number
  readonly periodMonth: number
  /** 월간 전달 기록 파이프라인 대상인지. false면 초기 진단(V0)이다. */
  readonly deliveryTracked: boolean
  /** 원장에게 전달한 기록이 남아 있는지. */
  readonly delivered: boolean
}

export const REPORT_REBUILD_DELIVERED_WARNING =
  '이미 원장님께 전달한 보고서입니다. 새 버전을 만들어도 전달 기록과 기존 파일은 그대로 남으며, 새 버전은 다시 전달해야 원장님께 닿습니다.'

/**
 * 초기 진단(V0)은 월간 생성 경로(`generate-monthly-report`)가 만들지 않는다.
 * 같은 버튼을 달면 "눌렀는데 이 달의 월간 보고서가 새로 생겼다"가 된다.
 */
const V0_UNAVAILABLE =
  '초기 진단은 월간 보고서 생성 경로로 다시 만들 수 없습니다. 운영 센터에서 초기 진단 재실행을 요청해 주세요.'

export function reportRebuildPlan(
  report: RebuildContract,
  now: Date = new Date(),
): ReportRebuildPlan {
  if (!report.deliveryTracked) {
    return { kind: 'unavailable', reason: V0_UNAVAILABLE }
  }
  // 서버도 같은 경계를 본다(require_closed_period). 먼저 판정해 400 대신 이유를 보여준다.
  const blocked = reportMonthBlockReason(
    { year: report.periodYear, month: report.periodMonth },
    now,
  )
  if (blocked) {
    return { kind: 'unavailable', reason: REPORT_MONTH_BLOCK_MESSAGE[blocked] }
  }
  return {
    kind: 'available',
    warning: report.delivered ? REPORT_REBUILD_DELIVERED_WARNING : null,
  }
}
