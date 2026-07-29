// 개인정보 처리방침 공개 표면 회귀 — 렌더 결과로 검증한다.
//
// 이전 버전은 app/privacy/page.tsx의 **소스 텍스트**를 정규식으로 매칭했다. 소스에만
// 있고 렌더되지 않는 문자열(주석 처리된 줄, 쓰이지 않는 상수 배열)에도 통과하므로
// 이용자가 실제로 보는 고지를 전혀 보장하지 못했다. 여기서는 페이지 컴포넌트를 실제로
// 렌더해 노출되는 텍스트만 검사한다.
//
// node:test의 타입 스트리핑은 JSX를 변환하지 못하므로, Next가 이미 번들로 들고 있는
// SWC로 .tsx를 변환하는 최소 로더를 등록한다 (추가 의존성 없음).
import assert from 'node:assert/strict'
import { register } from 'node:module'
import test from 'node:test'

register(
  'data:text/javascript,' +
    encodeURIComponent(`
      import { readFileSync } from 'node:fs'
      import { createRequire } from 'node:module'
      import { fileURLToPath } from 'node:url'
      const require = createRequire(${JSON.stringify(import.meta.url)})
      const { transformSync } = require('next/dist/build/swc')
      export async function resolve(specifier, context, next) {
        // next/link 같은 확장자 없는 서브패스는 Node ESM이 그대로 못 푼다.
        if (/^next\\/[a-z-]+$/.test(specifier)) {
          try { return await next(specifier + '.js', context) } catch {}
        }
        return next(specifier, context)
      }
      export async function load(url, context, next) {
        if (url.endsWith('.tsx')) {
          const filename = fileURLToPath(url)
          const out = transformSync(readFileSync(filename, 'utf8'), {
            filename,
            jsc: {
              parser: { syntax: 'typescript', tsx: true },
              target: 'es2022',
              transform: { react: { runtime: 'automatic' } },
            },
            module: { type: 'es6' },
          })
          return { format: 'module', shortCircuit: true, source: out.code }
        }
        return next(url, context)
      }
    `),
)

function plain(fragment: string): string {
  return fragment
    .replace(/<[^>]*>/g, ' ')
    .replace(/&ldquo;|&rdquo;/g, '"')
    .replace(/&#x27;|&amp;/g, "'")
    .replace(/\s+/g, ' ')
    .trim()
}

async function renderPrivacyMarkup(): Promise<string> {
  const { renderToStaticMarkup } = await import('react-dom/server')
  const page = await import('../app/privacy/page.tsx')
  return renderToStaticMarkup(page.default())
}

async function renderPrivacyText(): Promise<string> {
  return plain(await renderPrivacyMarkup())
}

/** 렌더된 문단 단위 텍스트 — 서로 다른 문단의 단어가 우연히 이어 붙지 않도록. */
async function renderPrivacyParagraphs(): Promise<string[]> {
  const markup = await renderPrivacyMarkup()
  return [...markup.matchAll(/<p[^>]*>([\s\S]*?)<\/p>/g)].map((match) => plain(match[1]))
}

test('privacy disclosure names the Korean service runtime region', async () => {
  const text = await renderPrivacyText()

  assert.match(text, /asia-northeast3/)
  assert.match(text, /대한민국 서울/)
})

test('privacy disclosure no longer claims the retired us-central1 runtime', async () => {
  assert.doesNotMatch(await renderPrivacyText(), /us-central1/)
})

test('privacy disclosure describes object storage as a configurable region', async () => {
  // 런타임·DB는 서울 고정이고, 객체 저장소만 배포 설정에 따라 국내/멀티리전을 오간다.
  assert.match(await renderPrivacyText(), /멀티리전/)
})

test('privacy disclosure names the destination country in the same paragraph as Slack', async () => {
  // 문단 단위로 확인한다 — 전체 텍스트 매칭은 다른 문단의 "미국"을 주워 담아
  // 위탁 목록 항목에서 국가가 빠져도 통과했다. 대상은 "- ..." 형태의 수탁자 목록 항목.
  const slackEntries = (await renderPrivacyParagraphs()).filter(
    (paragraph) => paragraph.startsWith('-') && paragraph.includes('Slack'),
  )

  assert.ok(slackEntries.length > 0, '렌더된 처리방침에 Slack 수탁자 목록 항목이 없다')
  for (const entry of slackEntries) {
    assert.match(entry, /미국/, `Slack 수탁 항목에 이전 국가가 없다: ${entry}`)
  }
})

test('privacy disclosure states the cross-border transfer country explicitly', async () => {
  assert.match(await renderPrivacyText(), /이전 국가: 미국/)
})

test('privacy disclosure renders every numbered section to the reader', async () => {
  // 섹션 상수만 늘리고 렌더 루프에서 빠뜨리면 소스 정규식은 통과하지만 여기서 깨진다.
  const text = await renderPrivacyText()

  for (const heading of [
    '1. 수집하는 개인정보 항목',
    '3. 보유 및 이용 기간',
    '5. 제3자 제공 / 처리 위탁 / 국외 이전',
    '9. 개인정보 보호책임자',
  ]) {
    assert.ok(text.includes(heading), `렌더된 처리방침에 "${heading}" 섹션이 없다`)
  }
})

test('privacy disclosure states the retention period and the withdrawal channel', async () => {
  const text = await renderPrivacyText()

  assert.match(text, /180일 이내에 자동 파기/)
  assert.match(text, /privacy@motionlabs\.kr/)
})
