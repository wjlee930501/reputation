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
  // 사진 승인은 사람만 풀 수 있다 — 목록에서 사라지면 병원을 열기 전에는 알 수 없다(O-2).
  assert.match(hospitalsList, /visual_approval_missing\?\.length/)
  assert.match(hospitalsList, /사진 승인 대기/)
  assert.match(hospitalsList, /hospitals\/\$\{h\.id\}\/info/)
  // 링크는 사람이 결정할 문제에만 붙는다 — 연결됨·확인 중은 누를 곳이 아니다.
  assert.match(hospitalsList, /h\.domain_state\.kind === 'problem'/)
})

test('the hospital header speaks the same three states from one overview call', () => {
  assert.doesNotMatch(hospitalLayout, /summarizeHeaderProgress|ProgressDot/)
  assert.doesNotMatch(hospitalLayout, /초기 진단 보고서|초기 진단 리포트|콘텐츠 허브 준비/)
  assert.match(hospitalLayout, /\/admin\/hospitals\/\$\{hospitalId\}\/overview/)
  assert.match(hospitalLayout, /describePublicService\(overview\.public_service\)/)
  assert.match(hospitalLayout, /describeContentState\(overview\.content\)/)
  assert.match(hospitalLayout, /describeDomainState\(overview\.domain\)/)
  // overview만 실패해도 병원 이름은 남는다 — 한쪽 실패가 헤더 전체를 지우지 않는다.
  assert.match(hospitalLayout, /Promise\.allSettled/)
  // 공개 서비스 상태가 곧 이 병원의 상태다 — 옛 status 배지를 함께 두면 ACTIVE인데
  // 공개 페이지가 없는 병원이 '운영 중'으로 보인다.
  assert.doesNotMatch(hospitalLayout, /STATUS_LABELS/)
  // 못 받아온 상태를 조용히 비우지 않는다.
  assert.match(hospitalLayout, /상태 불러오기 실패/)
  assert.match(hospitalLayout, /다시 시도/)
  assert.match(hospitalLayout, /overviewFailed/)
  // 늦게 도착한 옛 응답이 방금 바뀐 상태를 덮지 않는다.
  assert.match(hospitalLayout, /request !== requestSeq\.current/)
  // 주소는 주소만 말한다 — 마지막 확인 시각은 자기 도메인 상태가 한 번만 말한다.
  assert.doesNotMatch(hospitalLayout, /readHospitalDomainStatus\(hospital\)\.detail\}/)
})
