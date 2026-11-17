/**
 * 병원 사이트 공통 문구(템플릿) — 레이아웃 도입 전 화면과 레이아웃 A/B/C가 같은 원문을 쓴다.
 *
 * 2026-11-12 결정: 레이아웃을 바꿔도 문구는 바꾸지 않는다. 문구를 레이아웃마다 따로 적으면
 * 어느 한쪽만 고쳐지는 일이 생기므로 원문은 여기 한 곳에 둔다.
 */

/* 임상적 주장(치료 순서·수술 여부 등)을 만들어 원장 발언처럼 노출하지 않는다.
   안내·설명·상담 방식에 대한 비임상 운영 원칙만 다룬다. */
export const CARE_PRINCIPLES_TITLE = '증상의 원인부터 확인합니다'

/* 승인된 공개 소개문(public_about)이 없을 때 지역·진료과 문장 뒤에 붙는 기본 리드. */
export const CARE_PRINCIPLES_LEDE = '어디가 왜 불편한지 먼저 살피고, 검사 결과와 치료 방법을 말씀드린 뒤 치료를 시작합니다.'

/* 2026-11-17 마케팅 요청으로 다시 썼다 — 옛 문구('확인된 정보만 안내합니다 — 이 페이지의 …
   출처와 업데이트 일자를 표기')는 병원이 환자에게 하는 말로 읽히지 않았다. 진료과와 무관하게
   읽히도록 특정 검사·치료 이름은 넣지 않는다. 병원마다 바꾸는 칸은 따로 둔다. */
export const CARE_PRINCIPLES = [
  {
    key: 'explain',
    title: '검사 결과를 쉽게 풀어 드립니다',
    body: '검사 결과와 치료 기간을 어려운 용어 없이 설명드립니다. 궁금한 점은 진료실에서 바로 물어보시면 됩니다.',
  },
  {
    key: 'verified',
    title: '병원이 확인한 정보로 안내합니다',
    body: '진료시간과 휴진일, 오시는 길은 병원이 직접 확인한 정보로 안내합니다.',
  },
  {
    key: 'decide',
    title: '치료 방법은 진찰 후 함께 정합니다',
    body: '같은 증상이어도 나이, 증상 정도, 생활 습관에 따라 맞는 치료가 다를 수 있습니다. 진찰 후 치료 방법을 함께 정합니다.',
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
export const DOCTOR_SECTION_TITLE = '의료진 소개'
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

/* 진료 영역 목록 페이지(/treatments) 안내문 — 2026-11-17 마케팅 요청으로 짧게 고쳤다. */
export const TREATMENTS_PAGE_NOTE = '진료 항목별로 어떤 질환을 보는지 정리했습니다. 치료 방법은 진찰 후 정합니다.'
