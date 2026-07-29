import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// 본문 렌더링은 fetchContent를 await 하는 async 서버 컴포넌트 안에 있어 렌더 기반 검증이
// 불가능하다(renderToStaticMarkup은 async 컴포넌트를 렌더하지 못한다). 소스 텍스트로 계약을 고정한다.
const articlePageSource = readFileSync(
  new URL('../app/[slug]/contents/[contentId]/page.tsx', import.meta.url),
  'utf8',
)

// ReactMarkdown의 a 오버라이드 블록만 잘라낸다 — 이미 보호되고 있는 참고자료 섹션의 링크가
// 섞여 들어와 통과하는 것을 막기 위해, 뒤따르는 table 오버라이드를 끝 앵커로 삼는다.
function markdownLinkOverride(): string {
  const start = articlePageSource.indexOf('a: ({')
  const end = articlePageSource.indexOf('table: ({')
  assert.ok(start >= 0, 'ReactMarkdown components에 a 오버라이드가 있어야 한다')
  assert.ok(end > start, 'a 오버라이드는 table 오버라이드보다 앞에 있어야 한다')
  return articlePageSource.slice(start, end)
}

test('LLM-generated body links go through the same href validation as curated references', () => {
  const override = markdownLinkOverride()
  // 참고자료와 동일하게 스킴 검증을 거친 href만 링크가 된다.
  assert.match(override, /const safeHref = safeExternalHref\(href\)/)
  assert.match(override, /href=\{safeHref\}/)
})

test('LLM-generated body links are nofollow and cannot reach window.opener', () => {
  const override = markdownLinkOverride()
  assert.match(override, /rel="noopener noreferrer nofollow"/)
  assert.match(override, /target="_blank"/)
})

test('an unsafe href renders as plain text instead of a link', () => {
  const override = markdownLinkOverride()
  assert.match(override, /if \(!safeHref\) return <>\{children\}<\/>/)
})
