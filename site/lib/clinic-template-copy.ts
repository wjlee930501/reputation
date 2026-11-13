/**
 * 병원 사이트 공통 문구(템플릿) — 레이아웃 도입 전 화면과 레이아웃 A/B/C가 같은 원문을 쓴다.
 *
 * 2026-11-12 결정: 레이아웃을 바꿔도 문구는 바꾸지 않는다. 문구를 레이아웃마다 따로 적으면
 * 어느 한쪽만 고쳐지는 일이 생기므로 원문은 여기 한 곳에 둔다.
 */

/* 임상적 주장(치료 순서·수술 여부 등)을 만들어 원장 발언처럼 노출하지 않는다.
   안내·설명·상담 방식에 대한 비임상 운영 원칙만 다룬다. */
export const CARE_PRINCIPLES_TITLE = '설명과 상담을 우선합니다'

export const CARE_PRINCIPLES = [
  {
    key: 'explain',
    title: '충분히 설명합니다',
    body: '검사와 진료 과정을 환자가 이해할 수 있는 언어로 설명하고, 궁금한 점을 확인한 뒤 진행합니다.',
  },
  {
    key: 'verified',
    title: '확인된 정보만 안내합니다',
    body: '이 페이지의 진료 안내와 의료 정보는 출처와 업데이트 일자를 함께 표기하며, 과장된 표현을 사용하지 않습니다.',
  },
  {
    key: 'decide',
    title: '상담으로 함께 결정합니다',
    body: '치료 방향은 글이 아니라 진료실에서의 상담과 개인별 상태 확인을 거쳐 결정됩니다.',
  },
] as const

/* 특정 검사 장비·치료 방식(X-ray·초음파·도수치료 등)을 병원이 제공하는 것처럼
   단정하지 않는다 — 프로파일에 없는 임상 정보는 일반적인 상담·진단·치료·관리
   단계로만 안내한다. */
export const CARE_FLOW_TITLE = '첫 방문부터 경과 관리까지, 진료 4단계'
export const CARE_FLOW_NOTE = '상담과 진료 확인을 바탕으로 단계적으로 진행합니다.'

export const CARE_STEPS = [
  {
    label: '01',
    aux: '첫 방문 시',
    title: '상담·문진',
    body: '증상과 불편한 점, 생활 습관과 병력을 충분히 듣고 확인합니다.',
  },
  {
    label: '02',
    aux: '진찰 후',
    title: '진단·원인 확인',
    body: '진찰 소견을 바탕으로 필요한 검사를 안내하고 증상의 원인을 확인합니다.',
  },
  {
    label: '03',
    aux: '결과 확인 후',
    title: '치료 계획 상담',
    body: '확인된 결과를 바탕으로 치료 선택지를 설명하고 함께 결정합니다.',
  },
  {
    label: '04',
    aux: '치료 후',
    title: '경과 확인·생활 관리',
    body: '치료 후 경과를 확인하고 일상에서의 관리 방법을 안내합니다.',
  },
] as const

/* 섹션 제목 — 레이아웃 도입 전 화면의 제목 그대로. 레이아웃이 새로 더한 칸(오늘 진료·진료표·
   최근 건강 정보)은 원래 없던 섹션이라 여기 두지 않는다. */
export const TREATMENT_SECTION_TITLE = '진료 영역'
export const DOCTOR_SECTION_TITLE = '진료를 담당하는 의료진'
export const QUESTIONS_SECTION_TITLE = '자주 확인하는 질문'
export const FAQ_SECTION_TITLE = '자주 묻는 질문과 답변 요약'
export const GALLERY_SECTION_TITLE = '병원 공간'

export function factsSectionTitle(hospitalName: string): string {
  return `${hospitalName} 기본 정보`
}

export const FAQ_SECTION_NOTE =
  '진료실에서 반복되는 질문에 대한 답변 요약입니다. 개인의 상태에 따라 판단이 달라질 수 있으니 자세한 내용은 진료 상담에서 확인해 주세요.'

export const TREATMENT_SECTION_NOTE =
  '병원에서 주로 진료하는 영역입니다. 증상과 치료 방법은 개인마다 다를 수 있으니 자세한 내용은 진료 상담에서 확인해 주세요.'

export const FACTS_SECTION_NOTE = '진료시간과 연락처, 위치를 한눈에 확인할 수 있습니다.'
