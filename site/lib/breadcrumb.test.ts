import assert from 'node:assert/strict'
import test from 'node:test'

import { buildBreadcrumbJsonLd } from './breadcrumb.ts'

const ROOT = 'https://reputation.motionlabs.kr/jang-clinic'

interface ListItem {
  '@type': string
  position: number
  name: string
  item?: string
}

function itemsOf(jsonLd: Record<string, unknown>): ListItem[] {
  return jsonLd.itemListElement as ListItem[]
}

test('every non-final ListItem carries an item URL', () => {
  // 아티클 breadcrumb: 홈 › 의료 정보 › (유형 — 링크 없음) › 제목
  const jsonLd = buildBreadcrumbJsonLd(
    [
      { label: '홈', href: ROOT },
      { label: '의료 정보', href: `${ROOT}/contents` },
      { label: 'FAQ' },
      { label: '수술 후 회복 기간은?' },
    ],
    ROOT,
  )
  const listItems = itemsOf(jsonLd)

  // item 없는 중간 항목이 하나라도 남으면 Google이 breadcrumb 결과를 통째로 버린다.
  const nonFinal = listItems.slice(0, -1)
  assert.ok(nonFinal.length > 0)
  assert.ok(
    nonFinal.every((entry) => typeof entry.item === 'string' && entry.item.length > 0),
    '마지막이 아닌 ListItem에는 반드시 item이 있어야 한다',
  )
  // 링크 없는 유형 라벨은 구조화 데이터에서 제외된다.
  assert.deepEqual(
    listItems.map((entry) => entry.name),
    ['홈', '의료 정보', '수술 후 회복 기간은?'],
  )
})

test('positions stay contiguous from 1 after dropping unlinked crumbs', () => {
  const jsonLd = buildBreadcrumbJsonLd(
    [
      { label: '홈', href: ROOT },
      { label: '의료 정보', href: `${ROOT}/contents` },
      { label: 'FAQ' },
      { label: '수술 후 회복 기간은?' },
    ],
    ROOT,
  )

  assert.deepEqual(
    itemsOf(jsonLd).map((entry) => entry.position),
    [1, 2, 3],
  )
})

test('the final crumb is kept even without an href', () => {
  const jsonLd = buildBreadcrumbJsonLd(
    [{ label: '홈', href: ROOT }, { label: '진료 안내' }],
    ROOT,
  )
  const listItems = itemsOf(jsonLd)

  assert.equal(listItems.length, 2)
  assert.equal(listItems[0].item, ROOT)
  assert.equal(listItems[1].name, '진료 안내')
  assert.equal(listItems[1].item, undefined)
})

test('relative hrefs resolve against the hospital root', () => {
  const jsonLd = buildBreadcrumbJsonLd([{ label: '홈', href: 'contents' }, { label: '현재' }], ROOT)
  assert.equal(itemsOf(jsonLd)[0].item, `${ROOT}/contents`)
})
