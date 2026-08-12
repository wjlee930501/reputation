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
  }
}

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

export function getContentOperationsState(item: ContentOperationsItem): Exclude<ContentOperationsFilter, 'all' | 'carried'> {
  if (item.status === 'PUBLISHED') {
    if (item.post_publish_reviewed_at) return 'published'
    if (item.display?.review?.notification_state === 'NOT_REQUIRED') return 'published'
    return item.display?.review?.notification_state === 'SENT'
      ? 'postReviewPending'
      : 'notificationPending'
  }
  if (item.status === 'REJECTED') return 'rejected'
  if (item.status === 'CANCELLED') return 'cancelled'
  if (!item.title) return 'notGenerated'
  if (!item.compliance?.publishable) return 'needsReview'
  return 'publishable'
}

export function matchesContentOperationsFilter(
  item: ContentOperationsItem,
  filter: ContentOperationsFilter,
): boolean {
  if (filter === 'all') return true
  if (filter === 'carried') return isCarriedOver(item)
  return getContentOperationsState(item) === filter
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
