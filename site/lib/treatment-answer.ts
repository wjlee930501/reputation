// 진료 항목 페이지의 "진료 기본 정보" 블록 — 결정적으로 만든 사실 문장과 표.
//
// 왜 필요한가. 월간 측정 질문은 모두 지역 추천형이다("{지역}에서 {진료} 받을 수 있는 병원
// 알려줘"). 2026-07-29 운영 측정에서 AI 답변이 병원 허브를 인용하면 93%가 병원을 언급했다.
// 진료 항목 페이지는 그 질문에 가장 가까운 병원 소유 페이지지만, 지금까지는 글 목록뿐이라
// "이 진료를 어디서·누가·어떻게 받는가"를 한 문단으로 답하는 문장이 없었다.
//
// 문장은 승인된 병원 정보(이름·주소·진료과·의료진·전화)로만 만든다. 모델이 쓰지 않으며
// 비교·우위·추천 표현을 넣지 않는다(의료법 제56조). 값이 없으면 그 문장을 생략한다.

export interface TreatmentAnswerPhysician {
  name: string
  suffix: string | null
  certifications: string[]
}

export interface TreatmentAnswerInput {
  hospitalName: string
  treatmentName: string
  address: string
  specialties: string[]
  physicians: TreatmentAnswerPhysician[]
  phone: string | null
  hasWeeklyHours: boolean
}

export interface TreatmentAnswerFact {
  label: string
  value: string
  /** 값 대신 진료시간·오시는 길 페이지로 연결한다. */
  linksToVisit?: boolean
}

export interface TreatmentAnswer {
  heading: string
  sentences: string[]
  facts: TreatmentAnswerFact[]
}

const MAX_PHYSICIANS = 3

function clean(value: string | null | undefined): string {
  return (value ?? '').replace(/\s+/g, ' ').trim()
}

function physicianLabel(physician: TreatmentAnswerPhysician): string {
  const name = clean(physician.name)
  if (!name) return ''
  const suffix = clean(physician.suffix)
  const certs = physician.certifications.map(clean).filter(Boolean).slice(0, 2)
  const base = suffix ? `${name} ${suffix}` : name
  return certs.length > 0 ? `${base}(${certs.join(', ')})` : base
}

export function buildTreatmentAnswer(input: TreatmentAnswerInput): TreatmentAnswer | null {
  const hospitalName = clean(input.hospitalName)
  const treatmentName = clean(input.treatmentName)
  if (!hospitalName || !treatmentName) return null

  const address = clean(input.address)
  const specialties = [...new Set(input.specialties.map(clean).filter(Boolean))]
  const physicians = input.physicians.map(physicianLabel).filter(Boolean).slice(0, MAX_PHYSICIANS)
  const phone = clean(input.phone)

  const sentences = [`${treatmentName} 진료는 ${hospitalName}에서 받을 수 있습니다.`]
  if (address) sentences.push(`병원 위치는 ${address}입니다.`)
  // 조사(이/가·로/으로)를 받침에 맞춰 고르지 않도록 '…입니다' 문형만 쓴다.
  if (physicians.length > 0) sentences.push(`진료를 맡는 의료진은 ${physicians.join(', ')}입니다.`)
  if (phone) sentences.push(`예약과 문의 전화는 ${phone}입니다.`)

  const facts: TreatmentAnswerFact[] = [{ label: '진료 항목', value: treatmentName }]
  if (specialties.length > 0) facts.push({ label: '진료과', value: specialties.join(' · ') })
  if (address) facts.push({ label: '위치', value: address })
  if (physicians.length > 0) facts.push({ label: '담당 의료진', value: physicians.join(', ') })
  if (phone) facts.push({ label: '전화', value: phone })
  if (input.hasWeeklyHours) {
    facts.push({ label: '진료시간', value: '요일별 진료시간 보기', linksToVisit: true })
  }

  return { heading: `${treatmentName} 진료 기본 정보`, sentences, facts }
}
