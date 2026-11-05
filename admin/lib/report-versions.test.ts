import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { ADMIN_COPY } from './admin-copy.ts'
import { annotateReportVersions, groupReportVersions, reportVersionBadge } from './report-versions.ts'
import { parseReport } from './report-review.ts'

const report = (id: string, createdAt: string, extra: Record<string, unknown> = {}) => ({
  id, periodYear: 2026, periodMonth: 10, isInitialReport: false, createdAt, version: null, ...extra,
})

test('같은 달·종류 안에서 만든 시각 순서로 버전을 세고 마지막이 최신이다(목록 응답에 버전 키가 없다)', () => {
  const reports = [
    report('c', '2026-11-05T10:00:00+09:00'),
    report('a', '2026-11-01T07:15:00+09:00'),
    report('b', '2026-11-03T09:00:00+09:00'),
    report('aug', '2026-09-01T07:15:00+09:00', { periodMonth: 8 }),
    report('v0', '2026-10-02T07:15:00+09:00', { isInitialReport: true }),
  ]
  const info = annotateReportVersions(reports)
  assert.deepEqual(info.get('a'), { version: 1, total: 3, latest: false, latestId: 'c' })
  assert.deepEqual(info.get('b'), { version: 2, total: 3, latest: false, latestId: 'c' })
  assert.deepEqual(info.get('c'), { version: 3, total: 3, latest: true, latestId: 'c' })
  assert.deepEqual(info.get('aug'), { version: 1, total: 1, latest: true, latestId: 'aug' })
  // 초기 진단(V0)은 같은 달이라도 월간과 다른 줄이다.
  assert.deepEqual(info.get('v0'), { version: 1, total: 1, latest: true, latestId: 'v0' })
  assert.equal(reportVersionBadge(info.get('c')), `${ADMIN_COPY.reportVersion} 3 · ${ADMIN_COPY.reportVersionLatest}`)
  assert.equal(reportVersionBadge(info.get('a')), `${ADMIN_COPY.reportVersion} 1 · ${ADMIN_COPY.reportVersionOlder}`)
  assert.equal(reportVersionBadge(undefined), '')
})

test('서버 버전이 일부에만 있으면 무시하고 만든 시각 순이라 입력 순서와 무관하다(Codex ux2-5)', () => {
  const a = report('A', '2026-11-01T00:00:00Z', { version: 3 })
  const b = report('B', '2026-11-02T00:00:00Z')
  const c = report('C', '2026-11-03T00:00:00Z', { version: 2 })
  for (const list of [[a, b, c], [a, c, b], [c, b, a]]) {
    const info = annotateReportVersions(list)
    assert.equal(info.get('C')?.latest, true, JSON.stringify(list.map((x) => x.id)))
    assert.deepEqual([info.get('A')?.version, info.get('B')?.version, info.get('C')?.version], [1, 2, 3])
    assert.deepEqual(groupReportVersions(list).map((row) => [row.latest.id, row.older.map((x) => x.id)]), [['C', ['B', 'A']]])
  }
})

test('서버가 버전을 주면 그 값을 쓰고, 같은 시각이면 id로 안정되게 정한다', () => {
  const info = annotateReportVersions([
    report('x', '2026-11-01T00:00:00Z', { version: 4 }),
    report('y', '2026-11-02T00:00:00Z', { version: 2 }), // 만든 시각은 늦지만 서버 버전이 낮다
  ])
  assert.equal(info.get('x')?.latest, true)
  assert.equal(info.get('x')?.version, 4)
  assert.equal(info.get('y')?.version, 2)
  const tie = annotateReportVersions([report('b', '2026-11-01T00:00:00Z'), report('a', '2026-11-01T00:00:00Z')])
  assert.equal(tie.get('b')?.latest, true)
  assert.equal(tie.get('a')?.version, 1)
})

test('목록은 최신 한 줄 + 접힌 이전 버전(새 것부터)이고 목록 순서를 지킨다', () => {
  const rows = groupReportVersions([
    report('oct3', '2026-11-05T10:00:00+09:00'),
    report('oct1', '2026-11-01T07:15:00+09:00'),
    report('sep1', '2026-10-01T07:15:00+09:00', { periodMonth: 9 }),
    report('oct2', '2026-11-03T09:00:00+09:00'),
  ])
  assert.deepEqual(rows.map((row) => [row.latest.id, row.older.map((item) => item.id)]), [['oct3', ['oct2', 'oct1']], ['sep1', []]])
})

test('상세 응답의 버전(review_evidence.version)과 목록의 version 키를 모두 읽는다', () => {
  const base = { id: 'r', hospital_id: 'h', period_year: 2026, period_month: 10, report_type: 'MONTHLY', created_at: '2026-11-01T00:00:00Z' }
  assert.equal(parseReport(base)?.version, null)
  assert.equal(parseReport({ ...base, version: 3 })?.version, 3)
  assert.equal(parseReport({ ...base, review_evidence: { version: 2, measurement: {}, notification: {} } })?.version, 2)
})

test('목록 카드는 최신·이전 버전 표시와 접기 버튼을, 다이얼로그는 옛 버전에서 고치기 대신 안내와 최신 열기를 보인다', () => {
  const list = readFileSync(new URL('../app/hospitals/[id]/reports/ReportList.tsx', import.meta.url), 'utf8')
  const dialog = readFileSync(new URL('../app/hospitals/[id]/reports/ReportDialog.tsx', import.meta.url), 'utf8')
  const page = readFileSync(new URL('../app/hospitals/[id]/reports/page.tsx', import.meta.url), 'utf8')
  assert.match(list, /groupReportVersions\(reports\)/)
  assert.match(list, /data-testid="report-version-badge"/)
  assert.match(list, /data-testid="report-older-toggle"/)
  assert.match(list, /ADMIN_COPY\.reportOlderVersionsShow\b/)
  assert.match(list, /ADMIN_COPY\.reportOlderVersionsHide\b/)
  assert.match(dialog, /data-testid="dialog-version-badge"/)
  assert.match(dialog, /showPanel \? \(\s*<ReportAeEditPanel/)
  assert.match(dialog, /data-testid="ae-edit-not-latest"/)
  assert.match(dialog, /ADMIN_COPY\.aeEditNotLatest\b/)
  assert.match(dialog, /onOpenLatest\(versionInfo\.latestId\)/)
  assert.match(page, /versionInfo=\{annotateReportVersions\(reports\)\.get\(selected\.id\)\}/)
})
