export interface RealLeadSummary {
  total: number
  needs_attention: number
  overdue: number
  operations_test: number
}

export interface InquiryLeadLike {
  clinic_type?: string | null
  source?: string | null
  contact?: string | null
  question?: string | null
}

export interface InquiryDetails {
  address: string | null
  directorName: string | null
  homepage: string | null
  contact: string | null
}

const INQUIRY_LABELS: Record<string, keyof Omit<InquiryDetails, 'contact'>> = {
  '병원 주소': 'address',
  주소: 'address',
  '원장님 성함': 'directorName',
  '원장 성함': 'directorName',
  원장명: 'directorName',
  '병원 홈페이지': 'homepage',
  홈페이지: 'homepage',
}

export function isIntroductionInquiry(lead: InquiryLeadLike): boolean {
  return lead.clinic_type?.trim() === '도입문의' || lead.source?.trim() === 'INQUIRY'
}

/** Parse the labelled legacy `question` payload produced by the five-field inquiry form. */
export function readInquiryDetails(lead: InquiryLeadLike): InquiryDetails {
  const details: InquiryDetails = {
    address: null,
    directorName: null,
    homepage: null,
    contact: lead.contact?.trim() || null,
  }

  for (const line of (lead.question ?? '').split(/\r?\n/)) {
    const match = line.match(/^\s*([^:：]+?)\s*[:：]\s*(.*?)\s*$/)
    if (!match) continue
    const key = INQUIRY_LABELS[match[1].trim()]
    const value = match[2].trim()
    if (key && value && details[key] === null) details[key] = value
  }

  return details
}

export function hasStructuredInquiryDetails(lead: InquiryLeadLike): boolean {
  if (lead.clinic_type?.trim() === '도입문의') return true
  const details = readInquiryDetails(lead)
  return Boolean(details.address || details.directorName || details.homepage)
}

export function leadEmptyState(attentionOnly: boolean, total: number) {
  if (attentionOnly) {
    return {
      title: `조건에 맞는 상담 요청이 없습니다 (전체 ${total}건)`,
      detail: '확인 필요는 신규 요청이거나 첫 연락 기한을 넘긴 미연락 상담 요청입니다.',
    }
  }
  return {
    title: '아직 접수된 상담 요청이 없습니다.',
    detail: '공개 페이지 문의 폼으로 들어온 상담 요청이 이곳에 쌓입니다.',
  }
}
