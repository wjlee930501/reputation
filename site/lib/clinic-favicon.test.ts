import assert from 'node:assert/strict'
import test from 'node:test'

import {
  clinicFaviconHref,
  clinicFaviconSpec,
  clinicFaviconVersion,
  clinicMonogramLetter,
} from './clinic-favicon.ts'
import { isReservedPath } from './host-routing.ts'

test('monogram uses the first renderable letter of the hospital name', () => {
  assert.equal(clinicMonogramLetter('서울W내과의원 위례점'), '서')
  assert.equal(clinicMonogramLetter('  (주)강심장내과의원'), '주')
  assert.equal(clinicMonogramLetter('365열린의원'), '3')
  assert.equal(clinicMonogramLetter('mediclinic'), 'M')
})

test('monogram is empty when the letter is outside the bundled font subset', () => {
  assert.equal(clinicMonogramLetter(''), '')
  assert.equal(clinicMonogramLetter(null), '')
  assert.equal(clinicMonogramLetter('漢陽醫院'), '')
  assert.equal(clinicMonogramLetter('ㄱㄴ의원'), '')
})

test('white text is kept whenever it reaches large-text contrast', () => {
  // 채도 높은 주황·청록도 흰 글자다 — 대비 최대 규칙이면 짙은 글자로 뒤집혔다.
  assert.equal(clinicFaviconSpec({ name: '강심장', brand_primary_color: '#F05030' }).foreground, '#FFFFFF')
  assert.equal(clinicFaviconSpec({ name: '연세', brand_primary_color: '#20A0A0' }).foreground, '#FFFFFF')
  assert.equal(clinicFaviconSpec({ name: '서울', brand_primary_color: '#103080' }).foreground, '#FFFFFF')
})

test('light brand colors switch to dark ink text', () => {
  assert.equal(clinicFaviconSpec({ name: '장앤김', brand_primary_color: '#F0C000' }).foreground, '#0A1B2F')
  assert.equal(clinicFaviconSpec({ name: '행복', brand_primary_color: '#D6A72C' }).foreground, '#0A1B2F')
})

test('missing or malformed brand color falls back to the clinic default', () => {
  assert.equal(clinicFaviconSpec({ name: '행복', brand_primary_color: null }).background, '#17365D')
  assert.equal(clinicFaviconSpec({ name: '행복', brand_primary_color: 'red' }).background, '#17365D')
  assert.equal(clinicFaviconSpec({ name: '행복', brand_primary_color: '#f05030' }).background, '#F05030')
})

test('version changes with the rendered look and nothing else', () => {
  const base = clinicFaviconSpec({ name: '강심장', brand_primary_color: '#F05030' })
  const sameLook = clinicFaviconSpec({ name: '강남', brand_primary_color: '#f05030' })
  const recolored = clinicFaviconSpec({ name: '강심장', brand_primary_color: '#103080' })
  assert.equal(clinicFaviconVersion(base), clinicFaviconVersion(sameLook))
  assert.notEqual(clinicFaviconVersion(base), clinicFaviconVersion(recolored))
})

test('favicon href stays on a reserved path so custom domains are not rewritten', () => {
  const hospital = { name: '강심장', brand_primary_color: '#F05030' }
  const icon = clinicFaviconHref('gangsimjang', hospital, 'icon')
  const apple = clinicFaviconHref('gangsimjang', hospital, 'apple')
  assert.match(icon, /^\/favicon\/gangsimjang\?v=[0-9a-z]+$/)
  assert.match(apple, /^\/favicon\/gangsimjang\?v=[0-9a-z]+&variant=apple$/)
  assert.equal(isReservedPath(new URL(icon, 'https://x.test').pathname), true)
})
