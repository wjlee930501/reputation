import assert from 'node:assert/strict'
import test from 'node:test'

import { buildSitemap } from './sitemap-builder.ts'
import type { SitemapScope } from './sitemap-host.ts'

const PLATFORM = 'https://reputation.motionlabs.kr'
const API_BASE = 'https://api.example.test'

function setEnv(name: 'NEXT_PUBLIC_SITE_URL' | 'NODE_ENV', value: string | undefined): void {
  if (value === undefined) {
    Reflect.deleteProperty(process.env, name)
    return
  }
  Object.defineProperty(process.env, name, {
    configurable: true,
    enumerable: true,
    value,
    writable: true,
  })
}

async function withPlatform<T>(fn: () => Promise<T>): Promise<T> {
  const originalSite = process.env.NEXT_PUBLIC_SITE_URL
  const originalNodeEnv = process.env.NODE_ENV
  try {
    setEnv('NODE_ENV', 'test')
    setEnv('NEXT_PUBLIC_SITE_URL', PLATFORM)
    return await fn()
  } finally {
    setEnv('NEXT_PUBLIC_SITE_URL', originalSite)
    setEnv('NODE_ENV', originalNodeEnv)
  }
}

// URL 패턴별로 응답을 라우팅하는 fetch 모의. hospital detail은 treatments를 포함해
// appendHospitalEntries의 2차 detail 조회를 생략시키고, contents는 빈 목록을 준다.
function installFetchMock(routes: {
  byDomain?: (host: string) => Response
  detail?: (slug: string) => Response
  contents?: Response
  hospitals?: Response
}): () => void {
  const originalFetch = globalThis.fetch
  const fetchMock: typeof fetch = async (input) => {
    const url = String(input)
    if (url.includes('/site/hospitals/by-domain/')) {
      const host = decodeURIComponent(url.split('/site/hospitals/by-domain/')[1])
      return routes.byDomain?.(host) ?? new Response('not found', { status: 404 })
    }
    if (url.includes('/contents?')) {
      return routes.contents ?? new Response(JSON.stringify([]), { status: 200 })
    }
    if (/\/hospitals\/[^/?]+$/.test(url)) {
      const slug = decodeURIComponent(url.split('/hospitals/')[1])
      return routes.detail?.(slug) ?? new Response('not found', { status: 404 })
    }
    if (url.endsWith('/hospitals')) {
      return routes.hospitals ?? new Response(JSON.stringify([]), { status: 200 })
    }
    return new Response('not found', { status: 404 })
  }
  globalThis.fetch = fetchMock
  return () => {
    globalThis.fetch = originalFetch
  }
}

test('host-scope sitemap excludes platform entries and lists only that hospital', async () => {
  await withPlatform(async () => {
    const restore = installFetchMock({
      byDomain: () => new Response(JSON.stringify({ slug: 'jang-clinic' }), { status: 200 }),
      detail: (slug) =>
        new Response(
          JSON.stringify({ slug, aeo_domain: 'clinic.example.com', treatments: [] }),
          { status: 200 },
        ),
      contents: new Response(JSON.stringify([]), { status: 200 }),
    })
    try {
      const scope: SitemapScope = { kind: 'host', hostname: 'clinic.example.com' }
      const entries = await buildSitemap(scope, API_BASE)
      const urls = entries.map((e) => e.url)

      // 계약: 커스텀 도메인 sitemap에는 플랫폼 루트/llms.txt를 넣지 않는다.
      assert.ok(!urls.includes(PLATFORM), '플랫폼 루트 URL이 포함되면 안 된다')
      assert.ok(!urls.includes(`${PLATFORM}/llms.txt`), '플랫폼 llms.txt가 포함되면 안 된다')
      // 병원 canonical(커스텀 도메인) 루트와 병원 llms.txt만 실린다.
      assert.ok(urls.includes('https://clinic.example.com/jang-clinic'))
      assert.ok(urls.includes('https://clinic.example.com/jang-clinic/llms.txt'))
      // 다른 병원 도메인은 물론 플랫폼 호스트의 URL도 전혀 없어야 한다.
      assert.ok(
        urls.every((u) => u.startsWith('https://clinic.example.com/')),
        '모든 URL이 이 병원의 커스텀 도메인이어야 한다',
      )
    } finally {
      restore()
    }
  })
})

test('host-scope sitemap returns an empty list when the domain is unregistered (no platform leak)', async () => {
  await withPlatform(async () => {
    const restore = installFetchMock({
      byDomain: () => new Response('not found', { status: 404 }),
    })
    try {
      const scope: SitemapScope = { kind: 'host', hostname: 'unknown.example.com' }
      const entries = await buildSitemap(scope, API_BASE)
      assert.deepEqual(entries, [], '미등록 도메인은 빈 sitemap이어야 한다(플랫폼 URL 노출 금지)')
    } finally {
      restore()
    }
  })
})

test('host-scope sitemap returns an empty list when apiBase is missing (no platform leak)', async () => {
  await withPlatform(async () => {
    const entries = await buildSitemap({ kind: 'host', hostname: 'clinic.example.com' }, null)
    assert.deepEqual(entries, [])
  })
})

test('all-scope sitemap keeps the platform base entries plus each hospital', async () => {
  await withPlatform(async () => {
    const restore = installFetchMock({
      hospitals: new Response(
        JSON.stringify([{ slug: 'jang-clinic', treatments: [] }]),
        { status: 200 },
      ),
      contents: new Response(JSON.stringify([]), { status: 200 }),
    })
    try {
      const entries = await buildSitemap({ kind: 'all' }, API_BASE)
      const urls = entries.map((e) => e.url)

      // 플랫폼 sitemap에는 플랫폼 루트 + /llms.txt가 그대로 유지된다.
      assert.ok(urls.includes(PLATFORM))
      assert.ok(urls.includes(`${PLATFORM}/llms.txt`))
      // aeo_domain 없는 병원은 플랫폼 호스트 경로로 실린다.
      assert.ok(urls.includes(`${PLATFORM}/jang-clinic`))
    } finally {
      restore()
    }
  })
})

test('all-scope sitemap falls back to platform base entries when apiBase is missing', async () => {
  await withPlatform(async () => {
    const entries = await buildSitemap({ kind: 'all' }, null)
    const urls = entries.map((e) => e.url)
    assert.deepEqual(urls, [PLATFORM, `${PLATFORM}/llms.txt`])
  })
})
