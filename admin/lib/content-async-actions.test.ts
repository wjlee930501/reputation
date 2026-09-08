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

// 재생성 폴링과 그 버튼은 콘텐츠 화면에서 사라졌다(운영 센터가 맡는다).
// 남아 있지 않다는 계약은 content-page-contract.test.ts가 지킨다.

test('a single-item refresh never replaces an open editor', () => {
  assert.match(contentPage, /if \(preserveOpenEditor && editMode\) return prev/)
})
