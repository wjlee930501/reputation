import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MEASUREMENT_TRIALS,
  answerDemo,
  answerExamples,
  ctaSection,
  faqItems,
  faqSection,
  funnelSection,

  pricingSection,
  heroScarcity,
  landingHero,
  limitItems,
  limitsSection,
  marketSection,
  measuredFigures,
  measurementSpec,
  operationSection,
  operationSteps,
  painPoints,
  painSection,
  platformShareSection,
  platformShares,
  previewSection,
  sceneSection,
} from './landing-copy.ts'

/** 화면에 실제로 올라가는 모든 문장. 새 섹션을 추가하면 여기도 넣어야 한다. */
const ALL_COPY = [
  landingHero.titleLead,
  landingHero.titleMain,
  landingHero.subcopy,
  landingHero.primaryCta,
  marketSection.label,
  marketSection.heading,
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
  ...painPoints.flatMap((p) => [p.quote, p.answer]),
  platformShareSection.nudge,
  platformShareSection.sourceNote,
  ...platformShares.flatMap((p) => [p.name, p.note ?? ""]),
  faqSection.label,
  faqSection.heading,
  pricingSection.label,
  pricingSection.heading,
  pricingSection.note,
  ...pricingSection.plans.flatMap((p) => [p.name, p.price, p.note]),



  funnelSection.label,
  funnelSection.heading,
  funnelSection.body,
  funnelSection.oursNote,
  funnelSection.restNote,
  funnelSection.caveat,
  ...funnelSection.slots.map((s) => s.name),
  funnelSection.slotsCaption,
  heroScarcity.note,
  previewSection.label,
  previewSection.heading,
  ...faqItems.flatMap((f) => [f.question, f.answer]),
  ...measuredFigures.flatMap((f) => [f.value, f.label, f.meaning, f.source]),
  measurementSpec.premise,
  measurementSpec.headline,
  measurementSpec.spec,
  measurementSpec.reproducibility,
  ...operationSteps.flatMap((s) => [s.label, s.title, s.body]),
  ...limitItems.flatMap((l) => [l.title, l.body]),
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
  const limitText = limitItems.map((l) => `${l.title} ${l.body}`).join(' ')
  assert.ok(limitItems.length >= 3)
  // 이 둘은 반드시 '못 하는 것'으로 밝혀야 한다.
  assert.match(limitText, /순위/)
  assert.match(limitText, /환자 수|내원/)
  // 각 항목이 실제로 부정·한계를 말하고 있는가.
  for (const item of limitItems) {
    assert.match(
      `${item.title} ${item.body}`,
      /(않습니다|못|밖입니다|걸러냅니다|다시 봅니다)/,
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

test('the evidence band still carries at least one thing we can point at', () => {
  // 전부 인용값이 되면 계기판은 남의 자료 모음이 된다. 왼쪽 판(측정 규약)이 우리 몫을
  // 들고 있어야 하고, 그 규약은 백엔드 계약과 묶여 있다(아래 규약 테스트가 잡는다).
  assert.ok(measurementSpec.spec.length > 0)
  assert.ok(measurementSpec.reproducibility.length > 0)
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
  assert.match(faqText, /측정이 안 된 경우|실패/)
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
    assert.match(example.answerClinic, /○○/)
    assert.ok(example.answerSources.length >= 1)
  }
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

test('the scarcity note carries the whole rule', () => {
  /**
   * "오늘 N분 남음" 배지를 뺐으므로 각주가 규칙 전부를 진다 — 몇 명까지인지,
   * 언제 다시 열리는지. 둘 중 하나가 빠지면 신청자가 헛걸음한다.
   */
  assert.match(heroScarcity.note, /20분|20곳/)
  assert.match(heroScarcity.note, /리셋|열립니다|초기화/)
})

test('the landing does not resurrect the countdown badge', () => {
  /**
   * 남은 자리를 실시간으로 보여주는 것 자체는 정직하지만, "N개 남음" 배지는 어느
   * 랜딩에나 붙어 있는 그로스해킹 클리셰라 값싸 보인다. 실시간 잔여 수는 접수 화면이
   * 담당한다.
   */
  assert.doesNotMatch(ALL_COPY, /남았습니다|남음/)
})

test('the reset time in the copy matches the code', () => {
  /**
   * 자리 경계는 백엔드의 `SLOT_RESET_HOUR_KST = 8`이 정한다
   * (backend/app/api/public/diagnosis.py, 그쪽 테스트가 값 8을 고정한다).
   * 여기서 다른 시각을 적으면 신청자가 안내받은 시각에 와서 마감 화면을 본다.
   */
  assert.match(heroScarcity.note, /아침 8시/)
  assert.doesNotMatch(heroScarcity.note, /자정/)
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

// ── 점유율 차트 = 비용 판단의 근거 ───────────────────────────────────
test('platform shares add up to a plausible whole', () => {
  const total = platformShares.reduce((sum, item) => sum + item.share, 0)
  assert.ok(Math.abs(total - 100) < 0.5, `점유율 합이 ${total}%입니다.`)
})

test('exactly the platforms we measure are marked as measured', () => {
  const measured = platformShares.filter((p) => p.measured).map((p) => p.name)
  // 프로덕션이 재는 것은 두 곳이다(backend config: OPENAI_MODEL_QUERY, GEMINI_MODEL).
  assert.deepEqual(measured.sort(), ['ChatGPT', 'Gemini'])
})

test('the measured platforms actually justify the coverage claim', () => {
  /**
   * 넛지가 성립하려면 **칠해진 면적이 실제로 대부분**이어야 한다. 점유율 데이터가
   * 갱신돼 두 곳 합이 낮아지면 "나머지에 돈 쓸 이유가 없다"는 문장은 근거를 잃는다.
   * 그때는 문장을 고쳐야 하므로 여기서 막는다.
   */
  const measuredTotal = platformShares
    .filter((p) => p.measured)
    .reduce((sum, item) => sum + item.share, 0)
  assert.ok(
    measuredTotal >= 75,
    `측정 대상 합이 ${measuredTotal}%로 떨어졌습니다 — 측정 범위 카피를 다시 써야 합니다.`,
  )
  /**
   * 카피의 숫자는 **차트가 실제로 인쇄하는 문자열과 같아야 한다.**
   *
   * 앞 버전은 `Math.round(measuredTotal)`을 찾았고, 그래서 제목의 "84%"가 통과했다.
   * 그런데 차트는 바로 아래에서 `toFixed(1)`로 "83.9%"를 찍는다 — 같은 화면에 84와
   * 83.9가 동시에 있었고 테스트가 그 상태를 승인하고 있었다. 자기에게 불리하게
   * 반올림하는 것으로 신뢰를 사는 페이지에서, 유일하게 올려 반올림한 곳이 제목이면 안 된다.
   */
  const rendered = `${measuredTotal.toFixed(1)}%`
  const coverageCopy = `${marketSection.heading} ${platformShareSection.nudge}`
  assert.ok(
    coverageCopy.includes(rendered),
    `커버리지 카피가 차트 값(${rendered})과 다릅니다: ${coverageCopy}`,
  )
})

test('the coverage nudge is framed as a cost decision, not a limitation', () => {
  assert.match(platformShareSection.nudge, /비용|예산/)
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

test('the hero instrument states the same measurement contract the backend runs', () => {
  /**
   * 계기판은 접힘 위에서 "이렇게 잰다"를 약속한다. 백엔드 규약은
   * `LEADGEN_QUERY_COUNT=3` × `LEADGEN_REPEAT_COUNT=3` × 플랫폼 2 = 18건이고
   * (`lead_diagnosis_engine.plan_measurements`가 "계획된 18건"을 만든다),
   * 여기 문자열이 어긋나면 랜딩과 실제 리포트가 다른 말을 하게 된다.
   *
   * 문자열을 통째로 고정하지 않고 **곱이 맞는지**만 본다 — 표현을 다듬을 때마다
   * 테스트가 깨지면 정작 지켜야 할 숫자를 못 지킨다.
   */
  // 각주(spec)에 적힌 인수들의 곱이 총 호출 수와 같아야 한다.
  const factors = (measurementSpec.spec.match(/\d+/g) ?? []).map(Number)
  assert.ok(factors.length >= 3, `규약에서 곱할 값을 찾지 못했습니다: "${measurementSpec.spec}"`)
  // 마지막 숫자(= 18)는 결과이므로 곱에서 제외한다.
  const product = factors.slice(0, 3).reduce((a, b) => a * b, 1)
  assert.equal(
    product,
    measurementSpec.totalCalls,
    `규약 "${measurementSpec.spec}"의 곱은 ${product}인데 총 호출 수는 ${measurementSpec.totalCalls}입니다.`,
  )
  assert.equal(measurementSpec.totalCalls, 18)
  assert.match(measurementSpec.spec, new RegExp(String(measurementSpec.totalCalls)))
  // 플랫폼당 호출 수 × 플랫폼 2 = 총 호출 수.
  assert.equal(measurementSpec.perPlatform * 2, measurementSpec.totalCalls)
})

test('the instrument headline carries exactly one number', () => {
  /**
   * Consumer Reports의 규칙을 테스트로 고정한다 — "Every refrigerator we test gets wired
   * up with 15 temperature sensors."처럼 헤드라인 문장에는 숫자가 하나여야 한다.
   * 둘이면 스펙시트가 되고 영이면 슬로건이 된다.
   *
   * 앞 버전(`질의 3 × 반복 3 × 모델 2`)은 한 문장에 숫자가 셋이었다. 카피를 다듬다가
   * 파라미터가 헤드라인으로 다시 올라오는 것을 막는다.
   */
  const numbers = measurementSpec.headline.match(/\d+/g) ?? []
  assert.equal(
    numbers.length,
    1,
    `헤드라인에 숫자가 ${numbers.length}개입니다(1개여야 함): "${measurementSpec.headline}"`,
  )
  // 그 하나는 플랫폼당 호출 수여야 한다 — 다른 숫자가 오면 규약과 어긋난다.
  assert.equal(Number(numbers[0]), measurementSpec.perPlatform)
})

test('the instrument states the premise before the number', () => {
  // 논증 없이 숫자만 던지면 "반복 3회"가 왜 필요한지 설명되지 않는다.
  assert.ok(measurementSpec.premise.length > 0)
  assert.doesNotMatch(measurementSpec.premise, /\d/, '전제 문장에는 숫자가 없어야 합니다.')
})

test('the instrument names the platforms we actually measure', () => {
  assert.match(measurementSpec.headline, /ChatGPT/)
  assert.match(measurementSpec.headline, /Gemini/)
  // 쓰지 않는 모델 이름이 새면 안 된다(실측 출처와 같은 규칙).
  assert.doesNotMatch(measurementSpec.models, /gpt-5-mini|gpt-4o|terra/)
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
  marketSection.heading,
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
