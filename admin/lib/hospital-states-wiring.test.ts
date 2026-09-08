import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const hospitalsList = readFileSync(new URL('../app/hospitals/page.tsx', import.meta.url), 'utf8')
const hospitalLayout = readFileSync(
  new URL('../app/hospitals/[id]/layout.tsx', import.meta.url),
  'utf8',
)

// 목록·헤더가 각자 판정하면 같은 병원이 화면마다 다른 상태로 보인다(PR-0A H-06).
// 두 화면 모두 서버 판정을 라벨로만 바꾼다.
test('the hospital list reads the server states instead of judging its own', () => {
  assert.doesNotMatch(hospitalsList, /summarizeHeaderProgress/)
  assert.doesNotMatch(hospitalsList, /CheckCell/)
  assert.doesNotMatch(hospitalsList, /isPubliclyServing\(h\)/)
  assert.doesNotMatch(hospitalsList, /h\.schedule_set/)
  assert.match(hospitalsList, /describePublicService\(h\.public_service_state\)/)
  assert.match(hospitalsList, /describeContentState\(h\.content_state\)/)
  assert.match(hospitalsList, /describeDomainState\(h\.domain_state\)/)
  assert.match(hospitalsList, /h\.open_exception_count/)
  assert.match(hospitalsList, /h\.ae_owner\?\.name/)
})

test('the hospital header speaks the same three states from one overview call', () => {
  assert.doesNotMatch(hospitalLayout, /summarizeHeaderProgress|ProgressDot/)
  assert.doesNotMatch(hospitalLayout, /초기 진단 보고서|초기 진단 리포트|콘텐츠 허브 준비/)
  assert.match(hospitalLayout, /\/admin\/hospitals\/\$\{hospitalId\}\/overview/)
  assert.match(hospitalLayout, /describePublicService\(overview\.public_service\)/)
  assert.match(hospitalLayout, /describeContentState\(overview\.content\)/)
  assert.match(hospitalLayout, /describeDomainState\(overview\.domain\)/)
  // overview만 실패해도 이름·상태 배지는 남는다 — 한쪽 실패가 헤더 전체를 지우지 않는다.
  assert.match(hospitalLayout, /Promise\.allSettled/)
})
