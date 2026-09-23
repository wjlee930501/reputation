import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MEASUREMENT_TRIALS,
  answerDemo,
  answerExamples,
  ctaSection,
  faqItems,
  faqSection,
  pricingSection,
  landingHero,
  limitItems,
  limitsSection,
  exposureGradeIndex,
  exposureGrades,
  instrumentSection,
  localSection,
  measuredFigures,
  operationSection,
  operationSteps,
  painPoints,
  painSection,
  previewSection,
  sceneSection,
} from './landing-copy.ts'

/** 화면에 실제로 올라가는 모든 문장. 새 섹션을 추가하면 여기도 넣어야 한다. */
const ALL_COPY = [
  landingHero.titleLead,
  landingHero.titleMain,
  landingHero.subcopy,
  landingHero.primaryCta,
  sceneSection.label,
  sceneSection.heading,
  sceneSection.askLine,
  ...sceneSection.steps,
  operationSection.label,
  operationSection.heading,
  limitsSection.label,
  limitsSection.heading,
  ctaSection.label,
  ctaSection.heading,
  ctaSection.body,
  ctaSection.primaryCta,
  ...ctaSection.notes,
  painSection.label,
  painSection.heading,
  painSection.punchline,
  ...painPoints.flatMap((p) => [p.quote, p.answer]),
  instrumentSection.label,
  instrumentSection.lead,
  localSection.label,
  localSection.heading,
  ...Object.values(localSection.legend),
  localSection.ours,
  localSection.mapNote,
  ...localSection.points.flatMap((p) => [p.title, p.body]),
  localSection.caveat,
  faqSection.label,
  faqSection.heading,
  pricingSection.label,
  pricingSection.heading,
  pricingSection.note,
  pricingSection.management,
  ...pricingSection.plans.flatMap((p) => [p.name, p.price, p.note]),
  previewSection.label,
  previewSection.heading,
  previewSection.note,
  previewSection.includesLabel,
  ...previewSection.includes,
  previewSection.gradeLabel,
  previewSection.explainLabel,
  previewSection.explain,
  ...exposureGrades.map((g) => g.label),
  ...faqItems.flatMap((f) => [f.question, f.answer]),
  ...measuredFigures.flatMap((f) => [f.value, f.label, f.meaning, f.source]),
  ...operationSteps.flatMap((s) => [s.label, s.title, s.body, s.output]),
  limitsSection.noLabel,
  limitsSection.keepLabel,
  ...limitItems.flatMap((l) => [l.title, l.body, l.keep]),
  answerDemo.disclaimer,
  ...answerExamples.flatMap((e) => [
    e.tag,
    e.question,
    e.answerIntro,
    e.answerClinic,
    e.answerReason,
    ...e.answerSources,
  ]),
].join(' ')

// ── 의료광고 금지 표현 ───────────────────────────────────────────────
// backend/CLAUDE.md의 FORBIDDEN_EXPRESSIONS와 같은 목록이다. 콘텐츠 생성물은 백엔드가
// 검사하지만 **랜딩 카피는 사람이 쓰므로 검사 대상이 없었다.** 여기가 그 검사다.
const FORBIDDEN = [
  '1등',
  '최고',
  '최우수',
  '유일',
  '완치',
  '100%',
  '성공률',
  '부작용 없는',
  '검증된',
  '가장 잘하는',
  '국내 최초',
  '세계 최초',
  '특허',
  '독보적',
]

test('landing copy contains no forbidden medical-ad expression', () => {
  for (const word of FORBIDDEN) {
    assert.ok(
      !ALL_COPY.includes(word),
      `금지 표현 "${word}"이 랜딩 카피에 있습니다.`,
    )
  }
})

/** 성과(순위·환자 유입)를 주장하는 문장인가. 부정형 여부는 호출부가 따로 본다. */
function isGrowthClaim(sentence: string): boolean {
  return (
    /(순위|환자 수|내원|신환|신규 ?환자)/.test(sentence)
    && /(보장|약속|늘|증가|유치)/.test(sentence)
  )
}

test('the growth guard catches the phrasing that already slipped through', () => {
  // 가드는 "현재 카피가 통과한다"만으로는 증명되지 않는다. 실제로 라이브에 올라갔던
  // 문장을 직접 먹여, 넓힌 목록이 그것을 잡는지 확인한다.
  assert.ok(
    isGrowthClaim('결국 신환 유치를 위한 AI 활용 전략이 필요합니다'),
    '라이브에 올라갔던 "신환 유치" 문장을 가드가 잡지 못합니다.',
  )
  // 정직한 부정문은 여전히 걸러지되(=후보로 잡히되) 부정 검사를 통과해야 한다.
  assert.ok(isGrowthClaim('환자 수 증가 재지 않은 것을 성과로 적지 않습니다.'))
  // 성과와 무관한 문장은 애초에 후보가 아니다.
  assert.ok(!isGrowthClaim('환자가 실제로 묻는 질문을 골라 답을 씁니다.'))
})

test('landing copy never promises rank or patient volume', () => {
  /**
   * **부정문은 허용해야 한다.** "노출 순위를 보장하지 않습니다"는 지켜야 할 문장이고,
   * 금지 대상은 "보장합니다"뿐이다. 단순 금지 패턴을 쓰면 정직한 문장이 걸린다 —
   * 실제로 첫 버전이 그렇게 실패했다.
   *
   * 그래서 '순위/환자 수 + 약속 동사'가 나오는 문장을 모두 뽑아 **각각 부정형인지**
   * 확인한다.
   *
   * 주어·동사 목록은 실제 사고를 겪고 넓혔다. "결국 신환 유치를 위한 AI 활용 전략이
   * 필요합니다"가 라이브에 올라가 있었는데, `신환`이 주어 목록에 없고 `유치`가 동사
   * 목록에 없어 그대로 통과했다. 같은 페이지 아래에서는 "환자 수 증가는 재지 않습니다"라고
   * 적고 있었으므로, 이 가드가 막아야 했던 바로 그 자기모순이다.
   */
  const claimSentences = ALL_COPY.split(/(?<=[.!?])\s+/).filter(isGrowthClaim)
  assert.ok(claimSentences.length > 0, '순위·환자 수를 다루는 문장이 하나도 없습니다.')
  for (const sentence of claimSentences) {
    assert.match(
      sentence,
      /(않습니다|않고|아닙니다|다른 지표)/,
      `순위·환자 수를 약속하는 문장입니다: "${sentence.trim()}"`,
    )
  }
})

test('the page states explicitly what it does not do', () => {
  /**
   * 문구를 그대로 검사하지 않는다 — 카피를 다듬을 때마다 테스트가 깨지고, 정작
   * **무엇을 못 한다고 밝혔는가**는 검사하지 못한다. 항목의 존재와 부정 의미만 본다.
   */
  const limitText = limitItems.map((l) => `${l.title} ${l.body} ${l.keep}`).join(' ')
  assert.ok(limitItems.length >= 3)
  // 이 둘은 반드시 '못 하는 것'으로 밝혀야 한다.
  assert.match(limitText, /순위/)
  assert.match(limitText, /환자 수|내원/)
  // 각 항목이 실제로 부정·한계를 말하고 있는가.
  for (const item of limitItems) {
    assert.ok(item.keep.length > 0, `"${item.title}"에 대신 지키는 것이 없습니다.`)
    assert.match(
      `${item.title} ${item.body}`,
      /(않습니다|아닙니다|없습니다|못|밖입니다|걸러냅니다|확인합니다)/,
      `"${item.title}"이 무엇을 못 하는지 말하지 않습니다.`,
    )
  }
})

// ── 숫자에는 출처가 붙는다 ───────────────────────────────────────────
// 출처 없는 숫자는 근거가 아니라 광고 문구다. 타입이 필드를 강제하지만, 값이
// 비어 있거나 "자체 조사" 한마디로 때우는 것은 타입이 막지 못한다.

test('every figure in the evidence band declares whose number it is', () => {
  /**
   * **앞 가드는 "자체 실측만"이었다.** 이유는 "남의 숫자가 우리 숫자 옆에 있으면
   * 어느 쪽이 우리 근거인지 흐려진다"였고, 그 걱정 자체는 지금도 맞다.
   *
   * 계기판 오른쪽이 외부 조사로 바뀌면서 규칙을 바꾼다 — 출처를 섞지 말라가 아니라
   * **출처를 반드시 밝히라**로. 흐려지는 것을 막는 장치는 세 겹이다:
   *   ① `measured` 플래그가 코드에서 소유를 구분하고
   *   ② 화면에서는 색이 구분하며(`data-measured="false"`면 파랑을 쓰지 않는다)
   *   ③ 인용값은 출처에 조사 기관과 모수를 반드시 적는다.
   * 이 테스트는 ①③을 잡는다.
   */
  assert.ok(measuredFigures.length >= 2)
  for (const figure of measuredFigures) {
    assert.equal(
      typeof figure.measured,
      'boolean',
      `"${figure.value}"이 자체 측정값인지 인용값인지 표시하지 않았습니다.`,
    )
    if (figure.measured) {
      assert.match(figure.source, /실측/, `"${figure.value}"이 자체 측정값임을 밝히지 않았습니다.`)
    } else {
      // 인용값은 조사 주체와 모수가 함께 적혀야 한다. 모수를 빼면 78.1%(전체 성인)와
      // 60%(AI 이용자)가 한 모집단의 값처럼 읽힌다.
      assert.doesNotMatch(
        figure.source,
        /실측/,
        `"${figure.value}"은 인용값인데 자체 실측이라고 적혀 있습니다.`,
      )
      assert.match(
        figure.source,
        /·/,
        `"${figure.value}"의 출처에 조사 주체와 모수가 함께 적혀 있어야 합니다: "${figure.source}"`,
      )
      assert.ok(
        figure.source.split('·')[0].trim().length > 0,
        `"${figure.value}"의 출처에 조사 주체가 없습니다.`,
      )
    }
  }
})

test('measured figures quote only the models we actually run', () => {
  /**
   * 실측 표에는 더 유리한 값이 있다 — gpt-5-mini의 잡음률 27%. 하지만 프로덕션은
   * gpt-5.6-luna와 gemini-3.6-flash를 쓴다. 쓰지 않는 모델의 좋은 숫자를 인용하면
   * 데이터를 파는 것이 아니라 인상을 파는 것이 된다.
   *
   * 프로덕션 모델은 backend/app/core/config.py의 OPENAI_MODEL_QUERY·GEMINI_MODEL이다.
   */
  const sources = measuredFigures.map((f) => f.source).join(' ')
  assert.doesNotMatch(sources, /gpt-5-mini/)
  assert.doesNotMatch(sources, /gpt-4o/)
  assert.doesNotMatch(sources, /terra/)
  // 모델명을 언급하는 출처가 있다면 운영 모델이어야 한다.
  if (/gpt-|gemini-/.test(sources)) {
    assert.match(sources, /gpt-5\.6-luna|gemini-3\.6-flash/)
  }
})

// ── 측정 방식 공개 (측정 규약 섹션 → FAQ로 흡수) ────────────────
test('the page discloses how the number is produced', () => {
  const faqText = faqItems.map((f) => `${f.question} ${f.answer}`).join(' ')
  // 반복 측정을 밝히지 않으면 한 번의 결과를 사실처럼 파는 것이 된다.
  assert.match(faqText, /반복 횟수|세 번|아홉 번/)
  // 병원명을 질의에 넣지 않는다는 사실은 측정이 성립하는 근거다.
  assert.match(faqText, /병원 이름을 (넣|물)/)
  // 측정 실패와 미언급을 구분한다는 약속.
  assert.match(faqText, /측정이 안 된 경우|실패|답을 하지 않았|응답을 받지 못/)
})

// ── AI 답변 예시 (의료광고법) ────────────────────────────────────────
test('answer demo is framed as an example without guaranteeing results', () => {
  assert.match(answerDemo.disclaimer, /예시/)
  assert.match(answerDemo.disclaimer, /보장되지 않/)
  assert.match(answerDemo.answerClinic, /○○/)
})

test('every specialty answer example stays a safe placeholder example', () => {
  assert.ok(answerExamples.length >= 3)
  for (const example of answerExamples) {
    assert.ok(example.tag.length > 0)
    assert.ok(example.question.length > 0)
    assert.doesNotMatch(
      example.question,
      /잘 보는|잘하는 곳|후기 좋은 곳|빨리 낫는/,
      `${example.tag}: 무료 진단 예시 질문에 과장·평판성 표현이 들어갔습니다.`,
    )
    assert.match(example.answerClinic, /○○/)
    assert.ok(example.answerSources.length >= 1)
  }
})

test('post-publication human review copy matches the sampling policy', () => {
  const reviewCopy = [
    ...operationSteps.map((s) => s.body),
    ...limitItems.flatMap((l) => [l.body, l.keep]),
    ...faqItems.map((f) => f.answer),
  ].join(' ')

  // 공개 뒤에 사람이 보긴 본다는 사실은 밝혀야 한다 — 없으면 "올리고 끝"으로 읽힌다.
  assert.match(reviewCopy, /공개(된)? 뒤|발행 (뒤|후)/)
  // **다만 표본이다.** 운영 계약(CLAUDE.md「후행 검수는 조건부 표본 확인이다」)이
  // 그렇게 돌아가므로, 전건을 사람이 본다고 적으면 카피가 제품보다 앞서 나간다.
  // 내부 용어("사후 점검 큐")는 원장에게 설명 없는 말이라 화면에서 걷어냈고,
  // 대신 표본이라는 사실 자체를 문구가 들고 있는지 본다.
  assert.match(reviewCopy, /표본/)
  assert.doesNotMatch(
    reviewCopy,
    /발행 뒤(?:에는)? 담당 (매니저|마케터)가 다시 봅니다|모든 글을.*사람|전건.*검수|모든 글은 담당 (매니저|마케터)가 확인/,
  )
})

// ── 구조 ─────────────────────────────────────────────────────────────
// ── 히어로 미리보기 카드의 예시 수치 ────────────────────────────────
test('hero preview counts stay inside the real denominator', () => {
  // 분모 9는 규약(질의 3개 × 반복 3회)에서 오는 실제 값이다. 넘으면 화면이 거짓말을 한다.
  for (const example of answerExamples) {
    assert.ok(
      example.counts.chatgpt <= MEASUREMENT_TRIALS && example.counts.chatgpt >= 0,
      `${example.tag}: ChatGPT 등장 횟수가 분모를 벗어났습니다.`,
    )
    assert.ok(
      example.counts.gemini <= MEASUREMENT_TRIALS && example.counts.gemini >= 0,
      `${example.tag}: Gemini 등장 횟수가 분모를 벗어났습니다.`,
    )
  }
})

test('hero preview counts do not read as a promised outcome', () => {
  /**
   * 예시 수치를 전부 높게 잡으면 고지와 무관하게 "이만큼 나온다"는 약속으로 읽힌다.
   * 절반 이하인 예시가 하나라도 있어야 이것이 측정 결과의 **형태**를 보여주는
   * 화면임이 유지된다.
   */
  const hasModest = answerExamples.some(
    (e) =>
      e.counts.chatgpt <= MEASUREMENT_TRIALS / 2 || e.counts.gemini <= MEASUREMENT_TRIALS / 2,
  )
  assert.ok(hasModest, '모든 예시가 높은 등장 횟수입니다 — 약속으로 읽힙니다.')
  const allMaxed = answerExamples.every((e) => e.counts.chatgpt === MEASUREMENT_TRIALS)
  assert.ok(!allMaxed)
})

test('the hero addresses the reader directly', () => {
  // 3인칭 설명문으로 열면 남 얘기로 읽힌다. 원장님을 직접 부르고 질문으로 넘긴다.
  const heroText = [landingHero.titleLead, landingHero.titleMain].join(' ')
  assert.match(heroText, /원장님/)
  assert.match(landingHero.titleMain, /\?$/)
})

test('the hero leaves a slot for the rolling AI logo', () => {
  /**
   * `{ai}` 자리표시자가 사라지면 로고가 문장 끝에 붙거나 통째로 빠진다. 조사 위치가
   * 곧 문장이므로("…{ai}에 병원을") 자리표시자와 그 뒤의 조사를 함께 고정한다.
   */
  assert.ok(landingHero.titleLead.includes('{ai}'), '{ai} 자리표시자가 없습니다.')
  assert.match(landingHero.titleLead, /\{ai\}에/)
})

test('the hero claim stays inside what we measured', () => {
  // 재지 않은 분포("대부분의 병원이 0번")를 주장하지 않는다.
  const heroText = [landingHero.titleLead, landingHero.titleMain].join(' ')
  assert.doesNotMatch(heroText, /대부분|거의 모든|모든 병원/)
})

test('every pricing tier includes direct MotionLabs marketer management', () => {
  assert.deepEqual(
    pricingSection.plans.map(({ name, price, monthlyContents, vatExcluded }) => ({
      name,
      price,
      monthlyContents,
      vatExcluded,
    })),
    [
      { name: 'Starter', price: '60만원', monthlyContents: 12, vatExcluded: true },
      { name: 'Grower', price: '90만원', monthlyContents: 16, vatExcluded: true },
      { name: 'Leader', price: '120만원', monthlyContents: 20, vatExcluded: true },
    ],
  )
  // 전담 관리 문구는 표 아래 한 줄이다 — 세 카드가 같은 문장을 하나씩 들고 있으면
  // 카드가 말해야 할 차이(편수·가격·추천 대상)가 반복 문구에 밀린다.
  assert.match(pricingSection.management, /전담 마케터.*직접.*소통/)
  for (const plan of pricingSection.plans) {
    assert.equal(plan.vatExcluded, true)
    assert.equal(
      'management' in plan,
      false,
      `${plan.name} 카드가 공통 문구를 따로 들고 있습니다 — pricingSection.management 한 줄로 모읍니다.`,
    )
    assert.doesNotMatch(plan.note, /월 \d+편|\d+편 발행/)
  }
})

// ── 통증은 당사자의 문장으로 ────────────────────────────────────────
test('pain points are quoted in the director voice, not our slogans', () => {
  assert.ok(painPoints.length >= 3)
  for (const point of painPoints) {
    // 원장님이 실제로 하는 말이어야 한다 — 해요/어요/네요 같은 구어 종결이 그 표식이다.
    assert.match(
      point.quote,
      /(어요|해요|네요|없어요|몰라요|나와요|겠어요)[.?]?$/,
      `우리 문체로 쓰인 인용문입니다: "${point.quote}"`,
    )
    // 통증만 적고 답을 안 적으면 불안만 남긴다.
    // 길이 기준은 '답이 있는가'를 보는 것이지 길게 쓰라는 뜻이 아니다. 한 문장이면 충분하다.
    assert.ok(point.answer.length >= 18, `"${point.quote}"에 답이 붙어 있지 않습니다.`)
  }
})

test('pain point answers do not promise an outcome', () => {
  const answers = painPoints.map((p) => p.answer).join(' ')
  assert.doesNotMatch(answers, /반드시|틀림없이|확실히 (오릅|늘)/)
})

// ── 측정 방식은 FAQ 한 항목에 모은다 ─────────────────────────────────
// 원장님께 모델명·반복 횟수는 설득 근거가 아니다. 그래도 실무자가 확인하려 할 때 답이
// 있어야 하므로 지우지 않고 FAQ "측정은 어떻게 하나요?"에 모은다.
const methodItem = faqItems.find((f) => /측정은 어떻게/.test(f.question))

test('the FAQ keeps the measurement contract the backend runs', () => {
  /**
   * 백엔드 규약은 `LEADGEN_QUERY_COUNT=3` × `LEADGEN_REPEAT_COUNT=3` × 플랫폼 2 = 18건이다
   * (`lead_diagnosis_engine.plan_measurements`). 등급의 분모 9도 여기서 온다.
   */
  assert.ok(methodItem, 'FAQ에 측정 방식 항목이 없습니다.')
  assert.match(methodItem.answer, /질문 3개/)
  assert.match(methodItem.answer, /세 번씩/)
  assert.match(methodItem.answer, /18번/)
  assert.equal(MEASUREMENT_TRIALS, 9)
  assert.match(methodItem.answer, new RegExp(`${MEASUREMENT_TRIALS}번`))
})

test('the method answer names both providers without a market-share claim', () => {
  assert.ok(methodItem)
  assert.match(methodItem.answer, /OpenAI API/)
  assert.match(methodItem.answer, /Google Gemini API/)
  assert.doesNotMatch(methodItem.answer, /83\.9|84%|점유율/)
})

test('the method answer states what the fixed panel does not represent', () => {
  assert.ok(methodItem)
  assert.match(methodItem.answer, /소비자용 앱/)
  assert.match(methodItem.answer, /개인화 노출/)
  assert.match(methodItem.answer, /실제 환자 유입/)
})

test('the page body leads with outcomes, not model names or trial counts', () => {
  /**
   * 사는 사람은 원장님이다. 본문(FAQ 밖)에 API 이름·모델명·"18번"·"9회" 같은 측정
   * 파라미터가 다시 올라오면, 원장님이 가장 먼저 읽는 숫자가 측정 횟수가 된다.
   */
  const body = ALL_COPY.replace(faqItems.map((f) => `${f.question} ${f.answer}`).join(' '), '')
  assert.doesNotMatch(body, /API|gpt-|gemini-\d/)
  assert.doesNotMatch(body, /\d+ ?(번|회)(씩| 물| 중| 등장|\b)/)
  assert.doesNotMatch(body, /[×x] ?\d/)
})

// ── 노출 등급 ───────────────────────────────────────────────────────
test('exposure grades cover every count from 0 to the denominator without gaps', () => {
  assert.equal(exposureGrades.length, 5)
  assert.deepEqual(
    exposureGrades.map((g) => g.label),
    ['매우 부족', '부족', '보통', '우수', '매우 우수'],
  )
  // 상한이 증가하고 마지막이 분모와 같아야 0~9 모든 값이 정확히 한 등급에 들어간다.
  for (let i = 1; i < exposureGrades.length; i++) {
    assert.ok(exposureGrades[i].max > exposureGrades[i - 1].max)
  }
  assert.equal(exposureGrades.at(-1)!.max, MEASUREMENT_TRIALS)
  assert.equal(exposureGradeIndex(0), 0)
  assert.equal(exposureGradeIndex(2), 0)
  assert.equal(exposureGradeIndex(3), 1)
  assert.equal(exposureGradeIndex(6), 2)
  assert.equal(exposureGradeIndex(7), 3)
  assert.equal(exposureGradeIndex(8), 4)
  assert.equal(exposureGradeIndex(9), 4)
})

// ── 지역 경쟁(선점) ─────────────────────────────────────────────────
test('the local-competition section argues structure without promising a result', () => {
  /**
   * "먼저 들어갈수록 유리한 구조"는 논리이지 우리가 측정한 값이 아니다. 숫자를 붙이지 않고,
   * 순위·노출을 보장하지 않는다는 줄로 잠근다.
   */
  const text = [localSection.heading, ...localSection.points.flatMap((p) => [p.title, p.body])].join(' ')
  assert.doesNotMatch(text, /\d/, '선점 논리에 근거 없는 숫자가 붙었습니다.')
  assert.doesNotMatch(text, /1위|반드시|확실히|보장합니다/)
  assert.match(localSection.caveat, /보장하지 않습니다/)
  assert.ok(localSection.picked.length >= 3 && localSection.picked.length <= 4)
  for (const name of localSection.picked) assert.match(name, /○○|△△|□□/)
})

// ── 리포트 전달 정책 ─────────────────────────────────────────────────
test('the report is delivered with a sales contact, not auto-mailed', () => {
  /**
   * 예전에는 신청하면 리포트를 자동 메일로 보냈다. 리포트만 받고 연락이 끊기는 경우가
   * 많아, 도입문의를 받은 뒤 담당자가 리포트를 만들어 연락과 함께 전달하는 구조로 바꿨다.
   * 옛 문구("15분 안에 메일로", "리포트를 메일로 보내드립니다")가 남으면 약속과 운영이 어긋난다.
   */
  assert.doesNotMatch(ALL_COPY, /15분|메일로 보내|자동으로 보내|순차 발송|선착순/)
  assert.match(ctaSection.body, /리포트/)
  assert.match(ctaSection.body, /담당/)
  assert.match(previewSection.note, /도입문의/)
})

// ── 말투: 흔한 "AI 최적화" 랜딩과 겹치는 말 ─────────────────────────
test('the copy avoids the stock vocabulary of generic AI-marketing pages', () => {
  const STOCK = [
    '최적화', '극대화', '혁신', '차별화', '시너지', '선도', '압도적', '탁월', '완벽',
    '패러다임', '생태계', '원스톱', '한 단계 도약', '다양한', '지속적으로', '적극적으로', '효율적으로',
  ]
  for (const word of STOCK) {
    assert.ok(!ALL_COPY.includes(word), `상투 표현 "${word}"이 랜딩 카피에 있습니다.`)
  }
})

// ── FAQ ─────────────────────────────────────────────────────────────
test('the FAQ answers the objections we would otherwise get on a call', () => {
  const questions = faqItems.map((f) => f.question).join(' ')
  assert.ok(faqItems.length >= 6)
  // 경쟁 서비스가 4개 플랫폼을 광고하므로 이 질문은 반드시 온다.
  assert.match(questions, /Perplexity|Claude/)
  // 순위 상승 여부는 가장 많이 받는 질문이고, 답이 "아닙니다"여야 한다.
  const rankItem = faqItems.find((f) => /순위/.test(f.question))
  assert.ok(rankItem, '순위에 대한 질문이 FAQ에 없습니다.')
  assert.match(rankItem.answer, /아닙니다|보장하지 않/)
})

test('the hero names the platforms we actually check', () => {
  assert.match(landingHero.subcopy, /ChatGPT/)
  assert.match(landingHero.subcopy, /Gemini/)
})

test('every measured figure carries a plain-language meaning', () => {
  /**
   * Cochrane 규칙 — 큰 숫자에는 "그래서 이게 큰 거냐"가 붙어야 한다.
   * `meaning`은 같은 데이터를 다시 말한 것이어야 하고, 새 숫자를 들여오면 안 된다.
   * 없는 분모를 지어내는 것이 이 페이지에서 가장 위험한 실수다.
   */
  for (const figure of measuredFigures) {
    assert.ok(figure.meaning.length > 0, `${figure.value}에 해석 기준이 없습니다.`)
    assert.notEqual(figure.meaning, figure.label)
  }
})

test('the fold label counts the hidden questions instead of hardcoding a number', () => {
  /**
   * 랜딩은 앞의 몇 개만 세워 두고 나머지를 접는다. 남은 개수를 문자열에 박아 두면
   * 질문을 하나 추가한 날 "질문 7개 더 보기"가 조용히 틀린 값이 된다.
   */
  assert.ok(
    faqSection.moreLabel.includes('{n}'),
    '접힌 질문 수 자리표시자 {n}이 없습니다 — 숫자를 직접 적으면 안 됩니다.',
  )
  assert.doesNotMatch(faqSection.moreLabel, /\d/)
})

test('every FAQ answer is substantive', () => {
  for (const item of faqItems) {
    assert.ok(item.answer.length >= 50, `"${item.question}" 답변이 너무 짧습니다.`)
  }
})

test('operation steps describe operation, not tooling', () => {
  const stepText = operationSteps.map((s) => `${s.label} ${s.title} ${s.body}`).join(' ')
  assert.ok(operationSteps.length >= 3)
  assert.match(stepText, /정리합니다|발행합니다|기록합니다/)
})


// ── 카피가 기계처럼 읽히지 않게 ────────────────────────────────────
//
// 첫 버전은 헤딩 10개가 전부 완결 서술문에 전부 `~습니다/세요`로 끝났고, "A가 아니라 B"를
// 다섯 번 썼으며, 화면 문구 101개 중 18개가 부정형이었다. 내용이 아니라 **리듬이 먼저
// 읽히는** 상태였고 그게 기계가 쓴 글처럼 보이는 이유였다.
//
// 아래 가드는 문장을 좋게 만들지는 못한다. 같은 수사가 다시 쌓이는 것만 막는다.

const HEADINGS = [
  sceneSection.heading,
  previewSection.heading,
  previewSection.includesLabel,
  ...previewSection.includes,
  localSection.heading,
  painSection.heading,
  operationSection.heading,
  limitsSection.heading,
  faqSection.heading,
  ctaSection.heading,
]

test('headings do not all share one ending', () => {
  /**
   * 이 가드는 **생성형 특유의 균일함**을 막으려고 만들었다. 처음에는 절반을 넘기지
   * 못하게 했는데, 지금 헤딩은 대표가 직접 쓴 문장이다. 저자가 고른 어조를 테스트가
   * 되돌리는 것은 가드의 목적이 아니다.
   *
   * 그래서 기준을 "전부 같은 박자는 아닐 것"으로 낮춘다 — 여덟 개가 모두 `~습니다`로
   * 끝나는 상태만 막는다. 질문형이 하나 이상 있어야 한다는 아래 가드가 남아 있어,
   * 페이지가 통째로 선언문이 되는 것은 여전히 걸린다.
   */
  const declarative = HEADINGS.filter((h) => /니다[.!?]?$/.test(h))
  const varied = HEADINGS.length - declarative.length
  assert.ok(
    varied >= 2,
    `헤딩 ${HEADINGS.length}개 중 ${declarative.length}개가 '~습니다'로 끝납니다 — 다른 박자가 둘은 있어야 합니다.`,
  )
})

test('at least one heading is a question', () => {
  // 질문이 하나도 없으면 페이지가 통째로 선언문이 된다.
  assert.ok(HEADINGS.some((h) => h.includes('?')), '질문형 헤딩이 하나도 없습니다.')
})

test('the "A가 아니라 B" construction is not a habit', () => {
  const uses = ALL_COPY.match(/아니라/g) ?? []
  assert.ok(uses.length <= 2, `'아니라' 구문이 ${uses.length}번 쓰였습니다.`)
})

test('negation is not the default sentence shape', () => {
  /**
   * 부정형 자체는 이 서비스의 정직함이라 지워선 안 된다("보장하지 않습니다").
   * 다만 그것이 문장의 기본형이 되면 전부 같은 소리로 읽힌다. 비율만 본다.
   */
  const sentences = ALL_COPY.split(/(?<=[.!?])\s+/).filter((t) => t.trim().length > 8)
  const negative = sentences.filter((t) => /(않습니다|없습니다|아닙니다)/.test(t))
  const ratio = negative.length / sentences.length
  assert.ok(
    ratio < 0.3,
    `문장의 ${Math.round(ratio * 100)}%가 부정형입니다(${negative.length}/${sentences.length}).`,
  )
})
