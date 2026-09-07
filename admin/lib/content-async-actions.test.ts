import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const contentPage = readFileSync(
  new URL('../app/hospitals/[id]/content/page.tsx', import.meta.url),
  'utf8',
)

function handler(name: string, nextName: string): string {
  const start = contentPage.indexOf(`async function ${name}`)
  const end = contentPage.indexOf(`async function ${nextName}`, start + 1)
  assert.ok(start >= 0, `${name} handler must exist`)
  assert.ok(end > start, `${nextName} must follow ${name}`)
  return contentPage.slice(start, end)
}

test('post-publish review never repeats a confirmed POST when its follow-up GET fails', () => {
  const source = handler('handlePostPublishReview', 'handleReject')
  const post = source.indexOf('post-publish-review')
  const success = source.indexOf("setActionSuccess('공개 내용 확인을 완료로 기록했습니다.')")
  const followUpGet = source.indexOf('const full = await fetchAPI<ContentItem>')
  const retryRead = source.indexOf('retryRefreshItem(itemId)')

  assert.ok(post >= 0)
  assert.ok(post < success && success < followUpGet && followUpGet < retryRead)
  assert.doesNotMatch(source.slice(followUpGet), /문제 없음.*다시 누르세요/)
})

test('regeneration polls the accepted run and refreshes without replacing an open editor', () => {
  assert.match(contentPage, /\/admin\/operations\/hospitals\/\$\{id\}\/runs\/\$\{trackedRun\.runId\}/)
  assert.match(contentPage, /\['REQUESTED', 'QUEUED', 'RUNNING'\]\.includes\(run\.state\)/)
  assert.match(contentPage, /refreshItem\(trackedRun\.itemId, \{ preserveOpenEditor: true \}\)/)
  assert.match(contentPage, /if \(preserveOpenEditor && \(editMode \|\| briefEditMode\)\) return prev/)
})

test('an active regeneration disables both duplicate content and image requests', () => {
  assert.match(contentPage, /disabled=\{actionLoading \|\| contentRegenerationActive\}/)
  assert.match(contentPage, /disabled=\{actionLoading \|\| imageRegenerationActive\}/)
  assert.match(contentPage, /contentRegenerationActive \? '새 초안 준비 중' : '즉시 재생성'/)
  assert.match(contentPage, /imageRegenerationActive \? '새 이미지 준비 중' : '이미지만 재생성'/)
})
