export interface RealLeadSummary {
  total: number
  needs_attention: number
  overdue: number
  operations_test: number
}

export function leadEmptyState(attentionOnly: boolean, total: number) {
  if (attentionOnly) {
    return {
      title: `조건에 맞는 리드가 없습니다 (전체 ${total}건)`,
      detail: '확인 필요는 신규 또는 첫 연락 기한을 넘긴 미연락 리드입니다.',
    }
  }
  return {
    title: '아직 접수된 리드가 없습니다.',
    detail: '공개 페이지 문의 폼으로 들어온 상담 요청이 이곳에 쌓입니다.',
  }
}
