/**
 * 보고서 목록의 버전 — 같은 해·월·종류 안에서 어느 것이 최신인지. 목록 응답에는 버전 키가 없으므로(운영 확인) 만든 시각 순서로 세고,
 * 서버가 버전을 주면(`version`) 그 값을 쓴다. 옛 버전은 '보고서 고치기'가 `AE_EDIT_NOT_LATEST`로 막히므로 화면이 먼저 안내한다.
 */

import { ADMIN_COPY } from './admin-copy.ts'

export type VersionedReport = {
  readonly id: string
  readonly periodYear: number
  readonly periodMonth: number
  readonly isInitialReport: boolean
  readonly createdAt: string
  /** 서버가 준 버전(상세 응답의 review.version이나 목록의 version). 없으면 null. */
  readonly version?: number | null
}

export type ReportVersionInfo = {
  readonly version: number
  readonly total: number
  readonly latest: boolean
  readonly latestId: string
}

export function reportVersionGroupKey(report: Pick<VersionedReport, 'periodYear' | 'periodMonth' | 'isInitialReport'>): string {
  return `${report.isInitialReport ? 'V0' : 'MONTHLY'}:${report.periodYear}-${report.periodMonth}`
}

function byCreated(a: VersionedReport, b: VersionedReport): number {
  if (a.createdAt !== b.createdAt) return a.createdAt < b.createdAt ? -1 : 1
  return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
}

/** 그룹 안 **모두**에 서버 버전이 있을 때만 버전으로 정렬·번호를 매긴다 — 일부만 있으면 입력 순서에 따라 결과가 달라지므로(Codex ux2-5) 전부 만든 시각 순이다. */
function groupOrder(list: readonly VersionedReport[]): (a: VersionedReport, b: VersionedReport) => number {
  const allVersioned = list.every((report) => typeof report.version === 'number' && report.version > 0)
  return (a, b) => (allVersioned && a.version !== b.version ? (a.version as number) - (b.version as number) : byCreated(a, b))
}

/** 보고서 id → 버전 정보. 같은 달·종류 안에서 1부터 세고 마지막이 최신이다. */
export function annotateReportVersions(reports: readonly VersionedReport[]): ReadonlyMap<string, ReportVersionInfo> {
  const groups = new Map<string, VersionedReport[]>()
  for (const report of reports) {
    const key = reportVersionGroupKey(report)
    const list = groups.get(key)
    if (list) list.push(report)
    else groups.set(key, [report])
  }
  const out = new Map<string, ReportVersionInfo>()
  for (const list of groups.values()) {
    const allVersioned = list.every((report) => typeof report.version === 'number' && report.version > 0)
    const sorted = [...list].sort(groupOrder(list))
    const latestId = sorted[sorted.length - 1].id
    sorted.forEach((report, index) => {
      const version = allVersioned ? (report.version as number) : index + 1
      out.set(report.id, { version, total: sorted.length, latest: report.id === latestId, latestId })
    })
  }
  return out
}

export type ReportVersionRow<T extends VersionedReport> = { readonly latest: T; readonly older: readonly T[] }

/** 목록을 최신 한 줄 + 접힌 이전 버전으로 — 목록의 순서(처음 나온 자리)를 지키고, 이전 버전은 새 것부터. */
export function groupReportVersions<T extends VersionedReport>(reports: readonly T[]): ReportVersionRow<T>[] {
  const info = annotateReportVersions(reports)
  const rows: { key: string; latest: T; older: T[] }[] = []
  const byKey = new Map<string, { key: string; latest: T; older: T[] }>()
  for (const report of reports) {
    const key = reportVersionGroupKey(report)
    let row = byKey.get(key)
    if (!row) {
      row = { key, latest: report, older: [] }
      byKey.set(key, row)
      rows.push(row)
    }
    if (info.get(report.id)?.latest) row.latest = report
    else row.older.push(report)
  }
  return rows.map((row) => {
    const order = groupOrder([row.latest, ...row.older])
    return { latest: row.latest, older: [...row.older].sort((a, b) => order(b, a)) }
  })
}

/** '버전 N · 최신' / '버전 N · 이전 버전' — 목록 카드와 다이얼로그 제목이 같은 글자를 쓴다. */
export function reportVersionBadge(info: ReportVersionInfo | undefined): string {
  if (!info) return ''
  return `${ADMIN_COPY.reportVersion} ${info.version} · ${info.latest ? ADMIN_COPY.reportVersionLatest : ADMIN_COPY.reportVersionOlder}`
}
