/**
 * Admin에서 반복해 쓰는 운영자용 표시 문구.
 * 내부 enum/API 필드명은 이 파일로 번역하지 않는다. 화면 제목과 설명만
 * 실제 운영자가 판단할 수 있는 말로 통일한다.
 */
export const ADMIN_COPY = {
  aiExposure: 'AI 답변 노출',
  aiMentionRate: '병원 언급률',
  aiMentionRateDetail: 'AI 답변에서 병원이 언급된 비율',
  evidence: '근거 자료',
  evidenceNote: '근거 노트',
  observedAt: '확인 시각',
  attribution: '연결 근거',
  workspace: '병원 운영 화면',
  dashboard: '운영 요약',
  schedule: '발행 일정',
  lead: '상담 요청',
  // 아래는 검토 §4에서 개념 하나에 변형이 여럿 붙어 있던 것을 하나로 묶은 값이다.
  // 새로 만드는 화면은 같은 뜻의 문구를 다시 짓지 말고 이 키만 읽는다. 통일 전 변형이
  // 새 화면에 남아 있는지는 scripts/check_user_facing_terms.py가 검사한다.
  publicPage: '병원 공개 페이지',
  publicUrl: '공개 주소',
  operatingStandard: '콘텐츠 운영 기준',
  initialReport: '초기 진단 보고서',
  monthlyReport: '보고서',
  plan: '요금제',
  monthlyArticles: '월 발행 편수',
  operator: '운영자',
  aeOwner: '담당 AE',
  postPublishReview: '공개 후 확인',
  postPublishReviewed: '확인 완료',
  // 병원 정보 자동 입력 — 시스템이 채운 칸의 출처 표시와 선택 항목 표시.
  autofillBadge: '자동 입력',
  // {source}·{value}는 provenanceConflictText가 채운다.
  provenanceConflict: '{source}에는 {value}(으)로 되어 있습니다.',
  optionalMark: '(선택)',
  // 원장 인터뷰 설문 카드.
  surveyTitle: '원장 인터뷰 설문',
  surveyHint: '설문 응답을 PDF·DOCX 파일이나 공유 링크로 올리면 진료 철학과 병원 특성의 근거로 자동 반영됩니다.',
  surveyFileLabel: '설문 파일 올리기',
  surveyFileSave: '설문 파일 저장',
  surveyLinkLabel: '설문 링크',
  surveyLinkSave: '링크 추가',
  surveyEmpty: '등록된 설문이 없습니다.',
  surveyUnreadable: '글자를 읽을 수 없는 PDF입니다. 글자가 선택되는 파일이나 링크로 다시 올려 주세요.',
  surveyFileSaved: '저장 완료. 처리는 자동으로 이어집니다.',
  surveyFileFailed: '파일 형식(PDF·DOCX)을 확인한 뒤 다시 저장하세요.',
  surveyLinkSaved: '링크를 추가했습니다. 처리는 자동으로 이어집니다.',
  surveyLinkFailed: '링크 공유 설정을 확인한 뒤 다시 추가하세요.',
  // 근거 문서 업로드 폼 — 서버가 받는 형식은 PDF·DOCX뿐이다.
  documentUploadHint: 'PDF·DOCX 문서를 올릴 수 있습니다. 여러 개를 한 번에 고를 수 있습니다.',
  documentUploadLabel: '올릴 문서 파일 (PDF·DOCX)',
  documentUploadFormatFailed: '파일 형식(PDF·DOCX)을 확인한 뒤 다시 저장하세요.',
  ackSmsFailed: '원장 안내 문자 미발송 — 직접 연락이 필요합니다.',
  ackSmsUnconfirmed: '원장 안내 문자 수신 불명(재발송 안 함) — 원장에게 직접 확인하세요.',
} as const

export function describeMentionRate(value: number | null): string {
  return value === null ? '측정 결과 없음' : `${value.toFixed(1)}%`
}

export function describeUnknownCount(value: number | null | undefined, noun: string): string {
  return value === null || value === undefined ? `${noun} 확인 안 됨` : `${value}${noun}`
}
