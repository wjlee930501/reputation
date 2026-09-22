'use client'

import { useCallback, useEffect, useState } from 'react'
import { fetchAPI } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import {
  isInProgress,
  measuredWith,
  parseManualDiagnosisHistory,
  type ManualDiagnosisRow,
} from '@/lib/manual-diagnosis-history'
import { safeOperatorError } from '@/lib/operations-journey'

const TONE_CLASS: Record<string, string> = {
  ok: 'bg-emerald-50 text-emerald-700',
  progress: 'bg-blue-50 text-blue-700',
  warn: 'bg-amber-50 text-amber-800',
  danger: 'bg-red-50 text-red-700',
  muted: 'bg-slate-100 text-slate-600',
}

/**
 * 만든 진단의 이력.
 *
 * 만든 뒤 "상담 요청에서 보세요"로 넘겼더니 리드 수십 건 속에서 방금 만든 것을 찾지
 * 못했다. 만든 화면이 만든 결과를 보여준다.
 *
 * 측정이 도는 동안에는 스스로 새로고침한다 — 진행 중인 건이 있을 때만 돈다.
 */
export function DiagnosisHistory({ reloadToken }: { reloadToken: number }) {
  const [rows, setRows] = useState<readonly ManualDiagnosisRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(parseManualDiagnosisHistory(await fetchAPI<unknown>('/admin/lead-diagnoses?limit=20')))
      setError(null)
    } catch {
      setError(
        safeOperatorError('leads', '‘목록 새로고침’을 눌러 주세요. 계속 실패하면 개발팀 문의용 정보를 전달하세요.'),
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load, reloadToken])

  // 측정·보고서 생성은 몇 분 걸린다. 진행 중인 건이 있을 때만 주기적으로 다시 읽는다 —
  // 전부 끝난 목록을 계속 폴링하면 서버만 두드린다.
  useEffect(() => {
    if (!rows.some(isInProgress)) return
    const timer = window.setInterval(() => void load(), 15000)
    return () => window.clearInterval(timer)
  }, [load, rows])

  return (
    <section className="mt-8" aria-labelledby="diagnosis-history-heading">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id="diagnosis-history-heading" className="font-bold text-slate-900">
            최근 만든 콜용 진단
          </h3>
          <p className="mt-1 text-sm leading-6 text-slate-600 [word-break:keep-all]">
            무엇으로 측정했는지와 진행 상태입니다. 보고서가 준비되면 여기서 바로 엽니다.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="min-h-11 rounded-lg border border-slate-200 bg-white px-4 text-sm font-bold text-slate-600"
        >
          목록 새로고침
        </button>
      </div>

      {error && (
        <p className="mt-4 rounded-lg bg-red-50 p-3 text-sm leading-6 text-red-700 [word-break:keep-all]" role="alert">
          {error}
        </p>
      )}

      {loading ? (
        <p className="mt-4 text-sm text-slate-500" role="status">목록을 불러오는 중입니다.</p>
      ) : rows.length === 0 ? (
        <p className="mt-4 rounded-xl border border-dashed border-slate-200 bg-white px-5 py-8 text-center text-sm text-slate-500">
          아직 만든 콜용 진단이 없습니다.
        </p>
      ) : (
        <ul className="mt-4 grid gap-3">
          {rows.map((row) => (
            <li
              key={row.id}
              className={`rounded-xl border p-4 ${
                row.superseded ? 'border-slate-200 bg-slate-50' : 'border-slate-200 bg-white'
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-bold text-slate-900 [word-break:keep-all]">{row.clinicName}</p>
                  <p className="mt-1 break-keep text-xs text-slate-500">{measuredWith(row)}</p>
                  <p className="mt-0.5 text-xs text-slate-400">{formatDateTime(row.createdAt)}</p>
                </div>
                {row.reportHref && (
                  <a
                    href={row.reportHref}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex min-h-11 items-center rounded-lg border border-blue-200 bg-white px-4 text-sm font-semibold text-blue-700"
                  >
                    보고서 열기
                  </a>
                )}
              </div>

              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                {row.superseded && (
                  <span className="rounded-full bg-slate-200 px-2 py-0.5 text-[11px] font-semibold text-slate-700">
                    갈음됨 · 기록 보관
                  </span>
                )}
                {row.badges.map((badge) => (
                  <span
                    key={badge.axis}
                    className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${TONE_CLASS[badge.tone] ?? TONE_CLASS.muted}`}
                  >
                    {badge.axis} {badge.label}
                  </span>
                ))}
              </div>
              <p className="mt-2 break-keep text-xs leading-5 text-slate-600">{row.hint}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
