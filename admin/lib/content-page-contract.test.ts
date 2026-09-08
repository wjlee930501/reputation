// 콘텐츠 화면의 소스 계약 — 사람이 누를 수 있는 것은 표본 확인과 본문 편집뿐이다.
// 운영 복구(발행·재생성·발행일 이동·항목 종료)는 운영 센터가 맡고 백엔드 라우트는 남아 있다.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const page = readFileSync(new URL('../app/hospitals/[id]/content/page.tsx', import.meta.url), 'utf8')

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

test('반려는 표본 확인 흐름 안에서만 쓰인다', () => {
  assert.match(page, /confirmAction, setConfirmAction\] = useState<'reject' \| null>/)
  const rejectCall = page.indexOf('handleReject(selected.id)')
  const sampleBlock = page.indexOf('문제 발견 · 비공개 후 재생성')
  assert.ok(rejectCall > 0 && sampleBlock > 0)
})

test('운영 센터 딥링크와 단건 새로고침은 유지된다', () => {
  assert.match(page, /\?content=|get\('content'\)/)
  assert.match(page, /refreshItem/)
})

test('편집 저장은 PATCH 한 번이고, 이후 자동 재검수를 안내한다', () => {
  assert.match(page, /method: 'PATCH'/)
  assert.match(page, /저장하면 자동 안전검사·재검수, 공개 글은 이미지 재인증까지 자동으로 진행됩니다/)
})
