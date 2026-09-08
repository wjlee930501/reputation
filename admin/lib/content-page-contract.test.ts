// 콘텐츠 화면의 소스 계약 — 사람이 누를 수 있는 것은 표본 확인과 본문 편집뿐이다.
// 운영 복구(발행·재생성·발행일 이동·항목 종료)는 운영 센터가 맡고 백엔드 라우트는 남아 있다.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../app/hospitals/[id]/content/page.tsx', import.meta.url), 'utf8')
const signals = readFileSync(
  new URL('../app/hospitals/[id]/content/ReadOnlySignals.tsx', import.meta.url),
  'utf8',
)

const REMOVED = [
  'handlePublish',
  'handleRegenerate',
  'handleRegenerateImage',
  'handleReschedule',
  'handleCancelSlot',
  'handleSaveBrief',
  'enterBriefEditMode',
  "'/publish'",
  "'/regenerate'",
  "'/reschedule'",
  "'/cancel'",
  "'/brief'",
  'regenerationRuns',
  'trackedRun',
  '즉시 재생성',
  '지금 발행',
  '발행일 옮기기',
  '콘텐츠 항목 종료',
  '콘텐츠 가이드 편집',
  'query-targets',
  'exposure-actions',
]

test('운영 복구 버튼과 그 요청 경로는 콘텐츠 화면에 없다', () => {
  for (const fragment of REMOVED) {
    assert.equal(page.includes(fragment), false, `콘텐츠 화면에 ${fragment}이(가) 남아 있다`)
  }
  // 템플릿 리터럴로 조립한 요청 경로까지 잡는다 (경로 끝이 따옴표·역따옴표).
  assert.doesNotMatch(page, /\/(publish|regenerate|regenerate-image|reschedule|cancel|brief)['"`]/)
})

test('행 상태는 서버 판정을 그대로 쓰고, 표본만 사람이 확인한다', () => {
  assert.match(page, /describeRowState/)
  assert.match(page, /canConfirmSample/)
  assert.match(page, /post-publish-review/)
  assert.match(page, /\/reject/)
  assert.match(page, /문제 없음 · 확인 완료/)
  assert.match(page, /문제 발견 · 비공개 후 재생성/)
})

test('공개 글의 비공개는 표본 여부와 무관하고, "문제 없음" 확인만 표본으로 제한한다', () => {
  // 결함이 확인된 공개 글은 표본이 아니어도 사람이 내릴 수 있어야 한다(의료광고 안전
  // 통제). 반대로 "문제 없음" 확인은 표본만 누른다 — 월 12~20번의 헛클릭을 만들지 않는다.
  assert.match(page, /confirmAction, setConfirmAction\] = useState<'reject' \| null>/)
  const start = page.indexOf(": selected.status === 'PUBLISHED' ? (")
  const end = page.indexOf("selected.status === 'CANCELLED' ?", start)
  assert.ok(start > 0 && end > start)
  const published = page.slice(start, end)

  const sampleGate = published.indexOf('selectedSample ? (')
  const confirm = published.indexOf('문제 없음 · 확인 완료')
  const notSample = published.indexOf('표본 확인 대상이 아닙니다')
  const reject = published.indexOf('문제 발견 · 비공개 후 재생성')

  // 확인 버튼은 표본 분기 안에 있고, 비공개 버튼은 그 분기가 모두 끝난 뒤에 온다.
  assert.ok(sampleGate > 0 && confirm > sampleGate)
  assert.ok(notSample > confirm)
  assert.ok(reject > notSample)
  assert.match(published, /setConfirmAction\('reject'\)/)
  // 되돌릴 수 없는 조작이므로 확인 대화상자를 거친다.
  assert.match(page, /role="alertdialog"/)
  assert.match(page, /handleReject\(selected\.id\)/)
})

test('운영 센터로 가는 링크는 실제로 열리는 주소다', () => {
  // `/operations` 한 화면 + 질의값이 전부다 — 병원별 하위 경로는 존재하지 않는다.
  assert.match(page, /hospitalOperationsHref\(id, '\/operations\?queue=incidents'\)/)
  assert.doesNotMatch(page, /href="\/operations\/hospitals\//)
})

test('운영 센터 딥링크와 단건 새로고침은 유지된다', () => {
  assert.match(page, /\?content=|get\('content'\)/)
  assert.match(page, /refreshItem/)
})

test('화면은 발행 요일 섹션과 하단 읽기 전용 신호를 함께 조립한다', () => {
  assert.match(page, /<ScheduleSection/)
  assert.match(page, /<ReadOnlySignals/)
})

test('하단 환자 질문·노출 보완 제안은 읽기만 한다', () => {
  assert.match(signals, /\/query-targets/)
  assert.match(signals, /\/exposure-actions\?limit=/)
  assert.match(signals, /측정 대상/)
  // GET 외의 요청은 한 건도 없다 — 이 화면은 정보로만 보여 준다.
  assert.doesNotMatch(signals, /method:/)
  assert.doesNotMatch(signals, /'POST'|'PATCH'|'DELETE'/)
  // 읽기 전용 섹션은 다른 화면에서 할 일을 지시하지 않는다.
  assert.doesNotMatch(signals, /질문 추가·수정/)
  // 지난달을 보고 있으면 "이번 달"이라고 쓰지 않는다.
  assert.match(signals, /isCurrentKoreanMonth\(year, month\)/)
})

test('편집 저장은 PATCH 한 번이고, 이후 자동 재검수를 안내한다', () => {
  assert.match(page, /method: 'PATCH'/)
  assert.match(page, /저장하면 자동 안전검사·재검수, 공개 글은 이미지 재인증까지 자동으로 진행됩니다/)
})
