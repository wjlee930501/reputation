import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

// 병원 엔티티(MedicalClinic) JSON-LD는 페이지 컴포넌트 안에서 조립된다.
// privacy 페이지처럼 렌더해서 검사할 수 없다 — 이 페이지들은 fetchHospital을 await 하는
// async 서버 컴포넌트라 renderToStaticMarkup으로 렌더되지 않는다. 그래서 소스 텍스트로 계약을 고정한다.
const visitPageSource = readFileSync(new URL('../app/[slug]/visit/page.tsx', import.meta.url), 'utf8')
const hubPageSource = readFileSync(new URL('../app/[slug]/page.tsx', import.meta.url), 'utf8')

test('the hub and visit MedicalClinic nodes share one @id', () => {
  assert.match(hubPageSource, /'@id': `\$\{hospitalRootUrl\}#clinic`/)
  assert.match(visitPageSource, /'@id': `\$\{hospitalRootUrl\}#clinic`/)
})

test('the visit node does not claim /visit as the clinic url', () => {
  // 같은 @id로 병합되는 노드가 서로 다른 url을 내면 답변 엔진이 병원 공식 URL을 /visit으로 볼 수 있다.
  assert.doesNotMatch(visitPageSource, /\n\s*url: `\$\{hospitalRootUrl\}\/visit`/)
  assert.match(visitPageSource, /\n\s*url: hospitalRootUrl,/)
  // 페이지별 URL은 엔티티 url이 아니라 mainEntityOfPage로 표현한다.
  assert.match(visitPageSource, /mainEntityOfPage: `\$\{hospitalRootUrl\}\/visit`/)
})

test('both nodes build the same PostalAddress shape', () => {
  for (const source of [hubPageSource, visitPageSource]) {
    assert.match(source, /streetAddress: hospital\.address/)
    assert.match(source, /addressCountry: 'KR'/)
    // region[0]/region[1] 보강 없이는 두 노드의 주소가 서로 다른 정밀도로 병합된다.
    assert.match(source, /\.\.\.buildAddressRegionFields\(hospital\.region\)/)
    assert.match(source, /buildAddressRegionFields.*from '@\/lib\/clinic-schema'/)
  }
})
