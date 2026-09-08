// 전월 이월 콘텐츠 처리 헬퍼.
// 반려된 슬롯이 월 경계를 넘어 이월되면 backend가 carried_over_from(원래 예정일)을 내려준다.
// 이월 슬롯은 다음 달에 가장 먼저 처리해야 하므로 목록 최상단으로 끌어올린다.

export interface CarriedOverItem {
  carried_over_from?: string | null
  status?: string
}

export type ContentOperationsFilter =
  | 'all'
  | 'carried'
  | 'publishable'
  | 'needsReview'
  | 'notGenerated'
  | 'notificationPending'
  | 'postReviewPending'
  | 'published'
  | 'rejected'
  | 'cancelled'

export interface ContentOperationsItem extends CarriedOverItem {
  title?: string | null
  post_publish_notified_at?: string | null
  post_publish_reviewed_at?: string | null
  // 사람이 보는 표본인지 — backend post_publish_review_policy가 단일 기준이다(M-21).
  post_publish_review_required?: boolean
  display?: {
    review?: {
      label?: string | null
      reason?: string | null
      publishable?: boolean | null
      notification_state?: 'PENDING' | 'SENDING' | 'RETRYING' | 'HOLD' | 'SENT' | 'FAILED' | 'MISSING' | 'NOT_REQUIRED'
      notification?: PublishNotificationPresentation
    } | null
  } | null
  compliance?: {
    publishable: boolean
    // 공개 사이트가 이 글을 실제로 내보내는지 (backend/app/services/content_visibility.py).
    // 여기서는 선택 필드다 — 이 인터페이스는 부분 응답도 받는 구조적 계약이며,
    // 판정이 없으면 보류로 닫는다.
    public_visibility?: { visible: boolean; blockers: string[]; blocker_labels: string[] }
  }
}

// 'withheld'는 필터 값이 아니라 상태다 — 공개 사이트가 숨기는 중인 발행 글.
export type ContentOperationsState = Exclude<ContentOperationsFilter, 'all' | 'carried'> | 'withheld'

export interface PublishNotificationPresentation {
  state: 'PENDING' | 'SENDING' | 'RETRYING' | 'HOLD' | 'SENT' | 'FAILED' | 'MISSING' | 'NOT_REQUIRED'
  label: string
  problem: string | null
  publication_impact: string
  next_action: string
  notification_id: string | null
  safe_error_code: string | null
}

const NOTIFICATION_FALLBACK: PublishNotificationPresentation = {
  state: 'NOT_REQUIRED',
  label: '자동 관제 중',
  problem: null,
  publication_impact: '콘텐츠 발행에는 영향이 없습니다.',
  next_action: '문제가 감지된 항목만 예외 큐에 표시됩니다.',
  notification_id: null,
  safe_error_code: null,
}

export function getPublishNotificationPresentation(
  item: ContentOperationsItem,
): PublishNotificationPresentation {
  return item.display?.review?.notification ?? NOTIFICATION_FALLBACK
}

export function getContentOperationsState(item: ContentOperationsItem): ContentOperationsState {
  if (item.status === 'PUBLISHED') {
    // 공개 페이지가 숨기는 중인 글은 발행·알림·확인 어느 정상 묶음에도 들어가지 않는다.
    // 확인이 끝났거나 알림이 밀린 것과 무관하게 이 사실이 먼저다(H-01).
    // 판정이 아예 없으면(구버전 응답·필드 누락) 공개 중이라고 단정하지 않는다 —
    // 모르는 상태를 초록으로 칠하면 admin만 공개라고 말하는 그 사고가 그대로 돌아온다.
    if (item.compliance?.public_visibility?.visible !== true) return 'withheld'
    if (item.post_publish_reviewed_at) return 'published'
    if (item.display?.review?.notification_state === 'NOT_REQUIRED') {
      // Slack 알림이 필요 없는 공개(수동 발행 등)라도 표본이면 backend 예외 큐가 센다.
      // 여기서 먼저 '공개 중'으로 접으면 화면 숫자가 큐보다 작아 AE가 대기 건을 못 본다.
      return item.post_publish_review_required === true ? 'postReviewPending' : 'published'
    }
    if (item.display?.review?.notification_state !== 'SENT') return 'notificationPending'
    // 확인 대기 집계는 backend 예외 큐(human_post_publish_review_predicate)와 같은 표본만
    // 세야 한다 — 아니면 화면 숫자가 큐보다 항상 크고, AE는 처리할 수 없는 건수를 본다.
    return item.post_publish_review_required === true ? 'postReviewPending' : 'published'
  }
  if (item.status === 'REJECTED') return 'rejected'
  if (item.status === 'CANCELLED') return 'cancelled'
  if (!item.title) return 'notGenerated'
  if (!item.compliance?.publishable) return 'needsReview'
  return 'publishable'
}

/** 필터·집계용 묶음. 공개 보류는 AE가 손대야 하는 예외이므로 '자동 발행 차단'과 함께 센다
 * — 별도 칩을 만들지 않되, 정상 발행/확인 대기 묶음에는 절대 넣지 않는다. */
export function getContentOperationsBucket(
  item: ContentOperationsItem,
): Exclude<ContentOperationsFilter, 'all' | 'carried'> {
  const state = getContentOperationsState(item)
  return state === 'withheld' ? 'needsReview' : state
}

export function matchesContentOperationsFilter(
  item: ContentOperationsItem,
  filter: ContentOperationsFilter,
): boolean {
  if (filter === 'all') return true
  if (filter === 'carried') return isCarriedOver(item)
  return getContentOperationsBucket(item) === filter
}

export function buildPublicContentUrl(domain: string | null | undefined, contentId: string): string | null {
  const normalized = domain?.trim().replace(/^https?:\/\//, '').replace(/\/$/, '')
  return normalized ? `https://${normalized}/contents/${encodeURIComponent(contentId)}` : null
}

export function isCarriedOver(item: CarriedOverItem): boolean {
  return Boolean(item.carried_over_from)
}

/** 이월 슬롯을 앞으로, 나머지는 기존 순서 그대로 유지한다 (안정 정렬). */
export function sortCarriedOverFirst<T extends CarriedOverItem>(items: T[]): T[] {
  const carried: T[] = []
  const rest: T[] = []
  for (const item of items) {
    if (isCarriedOver(item)) carried.push(item)
    else rest.push(item)
  }
  return [...carried, ...rest]
}

export function countCarriedOver(items: CarriedOverItem[]): number {
  return items.filter(isCarriedOver).length
}

/** 아직 발행되지 않은 이월 슬롯 수 — 대시보드 우선 처리 알림 기준. */
export function countUnpublishedCarriedOver(items: CarriedOverItem[]): number {
  return items.filter(
    (item) => isCarriedOver(item) && !['PUBLISHED', 'CANCELLED'].includes(item.status ?? ''),
  ).length
}

// 반려(reject_content)는 scheduled_date가 오늘 이하이면 내일로 발행 일정을 옮긴다(backend
// api/admin/content.py:reject_content). 월말에 반려하면 다음 달로 넘어가는데, 단건
// 새로고침(refreshItem)이 그 결과를 그대로 병합하면 다음 달 슬롯이 이번 달 화면에
// 유령처럼 남는다 — 병합 전 반드시 이 판정을 거쳐야 한다.
export interface ScheduledContentItem {
  scheduled_date?: string | null
}

/** 아이템의 scheduled_date가 화면이 보고 있는 연/월과 같은가.
 * 날짜를 파싱할 수 없으면(값 없음/형식 불명) 보수적으로 true를 반환해 기존 병합 동작을 유지한다. */
export function belongsToMonthView(
  item: ScheduledContentItem,
  viewYear: number,
  viewMonth: number,
): boolean {
  const match = /^(\d{4})-(\d{2})-\d{2}/.exec(item.scheduled_date ?? '')
  if (!match) return true
  const [, y, m] = match
  return Number(y) === viewYear && Number(m) === viewMonth
}
