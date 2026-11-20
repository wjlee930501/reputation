// 섹션 설정 초안 미리보기 `/preview/{token}` — 설계 5절·13절(3b ②).
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { isPrivateLinkPath } from './analytics.ts'
import { isReservedPath } from './host-routing.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROUTE = readFileSync(join(HERE, '..', 'app', 'preview', '[token]', 'page.tsx'), 'utf8')
const NEXT_CONFIG = readFileSync(join(HERE, '..', 'next.config.mjs'), 'utf8')

test('preview links never send their token to analytics', () => {
  assert.equal(isPrivateLinkPath('/preview/abc.def'), true)
  assert.equal(isPrivateLinkPath('/preview'), true)
  assert.equal(isPrivateLinkPath('/previews'), false)
  assert.equal(isPrivateLinkPath('/survey/abc'), true)
})

test('preview is a platform route on every host (not rewritten under a hospital slug)', () => {
  assert.equal(isReservedPath('/preview/abc.def'), true)
  assert.equal(isReservedPath('/previewer'), false)
})

test('the preview route renders the same home component, fresh and unindexed', () => {
  assert.match(ROUTE, /export const dynamic = 'force-dynamic'/)
  assert.match(ROUTE, /robots: \{ index: false, follow: false, nocache: true \}/)
  assert.match(ROUTE, /<HospitalHome hospital=\{hospital\} contents=\{contents\} slug=\{hospital\.slug\} \/>/)
  assert.match(ROUTE, /fetchSitePreview\(token\)/)
})

test('only /preview may be framed, and only by the admin origin; it is never cached or indexed', () => {
  assert.match(NEXT_CONFIG, /source: '\/:path\(\(\?!preview\/\)\.\*\)',\s*headers: securityHeaders/)
  assert.match(NEXT_CONFIG, /source: '\/preview\/:path\*',\s*headers: previewHeaders/)
  assert.match(NEXT_CONFIG, /\['X-Frame-Options', 'Content-Security-Policy', 'Referrer-Policy'\]\.includes\(header\.key\)/)
  assert.match(NEXT_CONFIG, /\{ key: 'Referrer-Policy', value: 'no-referrer' \}/)
  assert.match(NEXT_CONFIG, /frame-ancestors \$\{PREVIEW_FRAME_ANCESTORS\}/)
  assert.match(NEXT_CONFIG, /'https:\/\/admin\.reputation\.motionlabs\.kr'/)
  assert.match(NEXT_CONFIG, /\{ key: 'Cache-Control', value: 'no-store' \}/)
  assert.match(NEXT_CONFIG, /\{ key: 'X-Robots-Tag', value: 'noindex, nofollow' \}/)
  // 다른 경로는 여전히 어디에도 끼워 넣을 수 없다.
  assert.match(NEXT_CONFIG, /"frame-ancestors 'none'"/)
  assert.match(NEXT_CONFIG, /\{ key: 'X-Frame-Options', value: 'DENY' \}/)
})

test('preview photos skip the image optimizer and the optimizer refuses them', async () => {
  const { isPreviewAssetUrl } = await import('./site-preview-assets.ts')
  assert.equal(isPreviewAssetUrl('https://api.example/api/v1/public/site-preview/tok/assets/x'), true)
  assert.equal(isPreviewAssetUrl('https://api.example/api/v1/public/hospitals/demo/assets/x'), false)
  assert.equal(isPreviewAssetUrl(null), false)
  const slug = join(HERE, '..', 'app', '[slug]')
  for (const [dir, name] of [
    ['_components', 'ClinicHero.tsx'],
    ['_components', 'ClinicAvatar.tsx'],
    ['_components', 'ClinicGallery.tsx'],
    ['_layouts', 'HomeLayoutA.tsx'],
    ['_layouts', 'HomeLayoutC.tsx'],
  ]) {
    const source = readFileSync(join(slug, dir, name), 'utf8')
    const images = source.match(/<Image\b[\s\S]*?\/>/g) ?? []
    assert.ok(images.length > 0, name)
    for (const image of images) assert.match(image, /unoptimized=\{[^}]*isPreviewAssetUrl\(/, name)
  }
  const proxy = readFileSync(join(HERE, '..', 'proxy.ts'), 'utf8')
  assert.match(proxy, /pathname === '\/_next\/image' && isPreviewAssetUrl\(request\.nextUrl\.searchParams\.get\('url'\)\)/)
  assert.match(proxy, /matcher: \[[^\]]*'\/_next\/image'\]/)
})

test('the preview reads contents without the shared cache', () => {
  assert.match(ROUTE, /fetchContents\(hospital\.slug, 60, \{ cache: 'no-store' \}\)/)
})
