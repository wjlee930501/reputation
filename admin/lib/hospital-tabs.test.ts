import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { MOVED_INFO_ANCHORS, movedInfoAnchorTarget } from './hospital-tabs.ts'

const ID = 'h-1'

test('옮긴 구획의 옛 병원 정보 앵커는 새 탭의 같은 앵커로 간다', () => {
  assert.equal(movedInfoAnchorTarget(ID, '#info-brand'), '/hospitals/h-1/public-page#info-brand')
  assert.equal(movedInfoAnchorTarget(ID, '#info-photos'), '/hospitals/h-1/public-page#info-photos')
  assert.equal(movedInfoAnchorTarget(ID, '#info-sources'), '/hospitals/h-1/sources#info-sources')
  assert.equal(movedInfoAnchorTarget(ID, '#domain-setup'), '/hospitals/h-1/domain#domain-setup')
  assert.equal(movedInfoAnchorTarget(ID, '#search-ownership'), '/hospitals/h-1/domain#search-ownership')
})

test('병원 정보에 남은 구획과 빈·깨진 앵커는 그대로 둔다', () => {
  for (const hash of ['', '#', '#info-director', '#info-channels', '#info-treatments', '#%E0%A4%A']) {
    assert.equal(movedInfoAnchorTarget(ID, hash), null, hash)
  }
})

test('옮긴 앵커는 실제로 그 탭 화면이 그린다', () => {
  const sources: Record<string, string> = {
    'public-page': ['BrandSection.tsx', 'PhotosSection.tsx'].map(info).join('\n'),
    sources: info('SourcesSection.tsx'),
    domain: [
      readFileSync(new URL('../app/hospitals/[id]/domain/page.tsx', import.meta.url), 'utf8'),
      info('OwnershipVerificationSection.tsx'),
    ].join('\n'),
  }
  for (const [anchor, tab] of Object.entries(MOVED_INFO_ANCHORS)) {
    assert.match(sources[tab], new RegExp(`id="${anchor}"`), `${tab} 탭에 #${anchor}가 없다`)
  }
})

test('레이아웃 편집기는 주소를 옮기지 않고 공개 페이지 탭으로 켜진다', () => {
  const layout = readFileSync(new URL('../app/hospitals/[id]/layout.tsx', import.meta.url), 'utf8')
  assert.match(layout, /`\$\{tabHref\(hospitalId, 'info'\)\}\/layout`/)
  assert.match(layout, /return path === 'public-page'/)
})

function info(name: string): string {
  return readFileSync(new URL(`../app/hospitals/[id]/info/${name}`, import.meta.url), 'utf8')
}
