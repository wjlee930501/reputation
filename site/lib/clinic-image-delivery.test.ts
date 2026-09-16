import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { CLINIC_IMAGE_QUALITY, CLINIC_IMAGE_SIZES, readClinicImageStatus } from './clinic-image-delivery.ts'

const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8')
const cover = source('../app/[slug]/_components/ContentCover.tsx')
const avatar = source('../app/[slug]/_components/ClinicAvatar.tsx')

test('photography quality is bounded and format negotiation remains available', () => {
  assert.equal(CLINIC_IMAGE_QUALITY, 75)
  const config = source('../next.config.mjs')
  assert.match(config, /formats: \['image\/avif', 'image\/webp'\]/)
  assert.match(config, /qualities: \[75, 84\]/) // previously emitted URLs remain valid.
  assert.match(config, /minimumCacheTTL: 86400/) // no extended stale public-media window.
})

test('portrait sizes match the 104px mobile slot, not the viewport', () => {
  assert.match(CLINIC_IMAGE_SIZES.portraitSolo, /720px\) 104px/)
  assert.match(CLINIC_IMAGE_SIZES.portraitMultiple, /720px\) 104px/)
  assert.match(CLINIC_IMAGE_SIZES.portraitMultiple, /144px$/)
  assert.doesNotMatch(CLINIC_IMAGE_SIZES.portraitMultiple, /100vw/)
})

test('large cover styling never implicitly preloads a below-fold image', () => {
  assert.match(cover, /priority = false/)
  assert.match(cover, /loading=\{priority \? 'eager' : 'lazy'\}/)
  assert.doesNotMatch(cover, /priority=\{variant|loading=\{variant/)
})

test('the actual article and content-list lead opt into high priority', () => {
  for (const path of ['../app/[slug]/contents/[contentId]/page.tsx', '../app/[slug]/contents/_components/ContentsFeedView.tsx']) {
    assert.match(source(path), /variant="featured"\s+priority/)
  }
  assert.doesNotMatch(source('../app/[slug]/_components/FeaturedContent.tsx'), /\spriority[\s=]/)
})

test('SSR portrait discovery and source-scoped failure recovery are retained', () => {
  assert.doesNotMatch(avatar, /mounted|useEffect/)
  assert.match(avatar, /useClinicImage\(src\)/)
  const hook = source('./use-clinic-image.ts')
  assert.match(hook, /result.src === src/)
  assert.match(hook, /readClinicImageStatus\(node\)/)
})

test('covers retain the fallback during loading without hiding no-JS images', () => {
  assert.match(cover, /clinic-cover-motif/)
  assert.match(cover, /clinic-cover-watermark/)
  assert.doesNotMatch(cover, /!showImage|!image.showImage|opacity:/)
  assert.match(cover, /src=\{src\}/) // preserve original identity and certification query.
})

for (const [label, values, expected] of [
  ['not requested lazy image', [true, '', 0, 0], 'pending'],
  ['in flight', [false, 'https://asset.example/photo', 0, 0], 'pending'],
  ['cached valid image', [true, 'https://asset.example/photo', 640, 480], 'ready'],
  ['cached request failure', [true, 'https://asset.example/photo', 0, 0], 'failed'],
  ['blank placeholder', [true, 'https://asset.example/photo', 1, 1], 'failed'],
] as const) {
  test(label, () => {
    const [complete, currentSrc, naturalWidth, naturalHeight] = values
    assert.equal(readClinicImageStatus({ complete, currentSrc, naturalWidth, naturalHeight }), expected)
  })
}

// The library lead spans the page; it must not inherit a half-width home cover.
test('full-width library cover has its own responsive size role', () => {
  assert.match(CLINIC_IMAGE_SIZES.feed, /1130px$/)
  assert.notEqual(CLINIC_IMAGE_SIZES.feed, CLINIC_IMAGE_SIZES.featured)
  assert.match(source('../app/[slug]/contents/_components/ContentsFeedView.tsx'), /sizes=\{CLINIC_IMAGE_SIZES.feed\}/)
})
