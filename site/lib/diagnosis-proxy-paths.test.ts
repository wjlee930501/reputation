import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'

/**
 * BFF 프록시가 만드는 백엔드 경로 검증.
 *
 * ## 왜 이 테스트가 있나 (2026-07-30 프로덕션 사고)
 *
 * `getApiBase()`는 **이미 `/api/v1/public`까지 포함**한다. 그런데 진단 프록시 4개가
 * `${getApiBase()}/public/diagnosis/...`로 써서 실제 요청이
 * `/api/v1/public/public/diagnosis/slots` → **404**가 됐다.
 *
 * 타입 검사도, 빌드도, 기존 테스트도 이걸 못 잡았다 — 문자열 결합이라서다.
 * 배포 후 랜딩의 "남은 자리"가 안 뜨는 것으로 처음 드러났다.
 *
 * 그래서 **소스에서 실제 경로 표현식을 읽어** 검사한다. 프록시가 무슨 경로를 부르는지는
 * 런타임 전에 알 수 있는 사실이고, 그걸 확인하지 않으면 다음에 또 배포로 배운다.
 */

const ROUTES = [
  'app/api/diagnosis/route.ts',
  'app/api/diagnosis/slots/route.ts',
  'app/api/diagnosis/[token]/status/route.ts',
  'app/api/diagnosis/[token]/report/route.ts',
]

function upstreamExpressions(rel: string): string[] {
  const source = readFileSync(join(process.cwd(), rel), 'utf8')
  // `${getApiBase()}` 로 시작하는 템플릿 문자열 안의 경로 부분만 뽑는다.
  return [...source.matchAll(/\$\{getApiBase\(\)\}([^`]*)`/g)].map((m) => m[1])
}

test('every diagnosis proxy calls at least one backend path', () => {
  for (const rel of ROUTES) {
    assert.ok(upstreamExpressions(rel).length > 0, `${rel}: getApiBase() 호출을 찾지 못했다`)
  }
})

test('no diagnosis proxy re-adds the /public prefix that getApiBase already carries', () => {
  // getApiBase() = https://.../api/v1/public — 여기에 /public을 또 붙이면 404다.
  for (const rel of ROUTES) {
    for (const path of upstreamExpressions(rel)) {
      assert.ok(
        !path.startsWith('/public'),
        `${rel}: 경로가 /public으로 시작한다 (getApiBase가 이미 포함) → "${path}"`,
      )
    }
  }
})

test('diagnosis proxies target the diagnosis namespace', () => {
  for (const rel of ROUTES) {
    for (const path of upstreamExpressions(rel)) {
      assert.match(path, /^\/diagnosis(\/|$)/, `${rel}: 예상 밖 경로 "${path}"`)
    }
  }
})

test('the existing leads proxy shows the same convention', () => {
  // 기존 코드가 진실의 기준이다 — leads는 `${apiBase}/leads`로 쓴다(/public 없음).
  const source = readFileSync(join(process.cwd(), 'app/api/leads/route.ts'), 'utf8')
  assert.match(source, /\$\{apiBase\}\/leads/)
  assert.ok(!/\$\{apiBase\}\/public\//.test(source))
})
