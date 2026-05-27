export const landingHero = {
  titleLead: "이제 환자는 AI에게 병원을 묻습니다.",
  titleMain: "그 답변에 우리 병원이 보이게 만듭니다.",
  titleSupport: "전담 AE와 Agent가 매달 대신 운영합니다.",
  body:
    "병원이 가진 정보를 AI가 읽을 수 있는 두 번째 홈페이지로 만들고, MotionLabs가 매달 운영합니다.",
  primaryCta: "무료 AI 노출 진단",
  secondaryCta: "운영 방식 보기",
}

// Hero & demo section — "AI 답변 안의 병원" 결과를 시각화하는 예시 데이터.
// 모든 답변은 '예시'이며 노출 순위·결과를 보장하지 않는다(의료광고법 준수).
// 병원명은 실제 상호가 아닌 ○○ 플레이스홀더만 사용한다.

// AI 답변 패널이 렌더링하는 한 건의 대화 내용.
export type AnswerContent = {
  question: string
  answerIntro: string
  answerClinic: string
  answerReason: string
  answerSources: string[]
}

// 진료과 탭으로 전환 가능한 답변 예시(태그 포함).
export type AnswerExample = AnswerContent & { tag: string }

export const answerDemo = {
  heading: "환자가 AI에게 물을 때, 답변에 우리 병원이 있어야 합니다.",
  body: "검색 순위가 아니라, AI가 만들어 주는 답변 자체가 환자의 첫 인상입니다.",
  question: "강남에서 어깨 통증 비수술 치료 잘 보는 병원 알려줘",
  answerIntro: "강남 지역에서 어깨 통증을 비수술적으로 진료하는 병원으로는 다음을 참고해 보실 수 있습니다.",
  answerClinic: "○○정형외과의원",
  answerReason: "비수술 통증 치료 중심으로 어깨 질환 진료 안내와 의료진 이력이 정리되어 있습니다.",
  answerSources: ["병원 진료 안내", "의료진 소개", "어깨 통증 FAQ"],
  disclaimer: "예시 답변입니다. 실제 AI 답변과 노출 결과는 보장되지 않습니다.",
}

// 진료과별 답변 탐색기(answer 섹션)에서 탭으로 전환되는 예시들.
export const answerExamples: AnswerExample[] = [
  {
    tag: "정형외과·통증",
    question: "강남에서 어깨 통증 비수술 치료 잘 보는 병원 알려줘",
    answerIntro: "강남 지역에서 어깨 통증을 비수술적으로 진료하는 병원으로는 다음을 참고해 보실 수 있습니다.",
    answerClinic: "○○정형외과의원",
    answerReason: "비수술 통증 치료 중심으로 어깨 질환 진료 안내와 의료진 이력이 정리되어 있습니다.",
    answerSources: ["병원 진료 안내", "의료진 소개", "어깨 통증 FAQ"],
  },
  {
    tag: "내과·검진",
    question: "위내시경이랑 대장내시경 같이 받을 수 있는 병원 추천해줘",
    answerIntro: "위·대장내시경을 함께 받을 수 있는 병원으로는 다음을 참고해 보실 수 있습니다.",
    answerClinic: "○○내과의원",
    answerReason: "검진 프로세스와 장비, 사전 준비 안내가 정리되어 있습니다.",
    answerSources: ["검진 안내", "내시경 장비 소개", "검사 전 준비 FAQ"],
  },
  {
    tag: "산부인과",
    question: "생리불순 때문에 여성 검진 받을 산부인과 어디가 좋아?",
    answerIntro: "생리불순 상담과 여성 검진을 함께 보는 산부인과로는 다음을 참고해 보실 수 있습니다.",
    answerClinic: "○○여성의원",
    answerReason: "생리불순 진료 흐름과 검진 항목, 첫 방문 안내가 정리되어 있습니다.",
    answerSources: ["진료 안내", "여성 검진 항목", "생리불순 FAQ"],
  },
  {
    tag: "가정의학과/로컬 의원",
    question: "동네에서 만성질환 꾸준히 관리받을 만한 의원 알려줘",
    answerIntro: "거주 지역에서 만성질환을 지속적으로 관리할 수 있는 의원으로는 다음을 참고해 보실 수 있습니다.",
    answerClinic: "○○가정의학과의원",
    answerReason: "만성질환 관리, 예방접종, 검사 안내가 정리되어 있습니다.",
    answerSources: ["진료 안내", "만성질환 관리", "예방접종 안내"],
  },
]

export const proofItems = [
  {
    title: "AI 답변 진단",
    body: "ChatGPT·Gemini 답변 속 병원 노출을 점검합니다.",
  },
  {
    title: "두 번째 홈페이지",
    body: "AI가 읽을 수 있는 병원 정보 페이지를 따로 만듭니다.",
  },
  {
    title: "AE·Agent 운영",
    body: "툴을 배울 필요 없이 전담 인력이 대신 운영합니다.",
  },
  {
    title: "월간 리포트",
    body: "답변 변화와 다음 액션을 원장님께 전달합니다.",
  },
]

export const comparisonItems = [
  {
    label: "구독형 SaaS 툴",
    title: "병원이 직접 배우고 운영합니다.",
    points: [
      "도구 사용과 콘텐츠 검수가 병원 몫으로 남습니다.",
      "담당자가 바빠지면 발행과 점검이 밀립니다.",
    ],
  },
  {
    label: "Re:putation 방식",
    title: "전담 AE와 Agent가 대신 운영합니다.",
    points: [
      "병원 정보를 구조화된 공개 자산으로 정리합니다.",
      "콘텐츠 발행과 답변 점검을 매달 관리합니다.",
    ],
  },
]

export const processSteps = [
  {
    title: "병원 정보 정리",
    body: "약력·진료·지역 정보를 운영 형태로 정리합니다.",
  },
  {
    title: "두 번째 홈페이지",
    body: "AI가 크롤링하기 쉬운 AEO 페이지를 만듭니다.",
  },
  {
    title: "콘텐츠 운영",
    body: "요금제에 맞춰 매달 콘텐츠를 발행합니다.",
  },
  {
    title: "AI 답변 점검",
    body: "환자 질문에서 병원이 어떻게 보이는지 확인합니다.",
  },
  {
    title: "월간 리포트",
    body: "변화와 다음 액션을 원장님께 보고합니다.",
  },
]

export const trustItems = [
  {
    title: "노출·결과 미보장",
    body: "순위나 치료 결과를 보장하지 않습니다.",
  },
  {
    title: "공개 정보 기반",
    body: "검증된 병원 정보의 연결성만 다룹니다.",
  },
  {
    title: "AE 확인 후 발행",
    body: "자동 생성물을 사람이 검수합니다.",
  },
  {
    title: "원장 보고용 정리",
    body: "의사결정에 필요한 요약만 제공합니다.",
  },
]
