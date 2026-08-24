'use client'

import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import { ApiError, fetchAPI } from '@/lib/api'
import { fetchCurrentAccount } from '@/lib/current-account'
import {
  deliveryConflict,
  deliveryDeveloperNote,
  reportListDeveloperNote,
  type DeliveryIssue,
} from '@/lib/report-delivery'
import { parseReport, parseReports, type ReportView } from '@/lib/report-review'
import { preflightDeliveryAction } from '@/lib/report-component-behavior'
import { ReportList } from './ReportList'
import { type DeliveryAction } from './ReportDelivery'
import { ReportReviewDialog } from './ReportReviewDialog'
import { ReportRunStatus } from './ReportRunStatus'

const LOAD_ERROR: DeliveryIssue = {
  title: '최신 리포트 상태를 불러오지 못했습니다',
  problem: '서버에서 이 리포트의 최신 검수 근거를 확인하지 못했습니다.',
  customerImpact: '오래된 화면만 보고 원장 전달 여부를 결정할 수 없습니다.',
  nextAction: '‘최신 상태 다시 확인’을 누르세요. 계속 실패하면 개발팀 문의용 정보를 복사해 전달해 주세요.',
  action: 'developer',
}

export default function ReportsPage() {
  const { id: hospitalId } = useParams<{ id: string }>()
  const [reports, setReports] = useState<ReportView[]>([])
  const [selected, setSelected] = useState<ReportView | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingId, setLoadingId] = useState<string | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const [issue, setIssue] = useState<DeliveryIssue | null>(null)
  const [busy, setBusy] = useState(false)
  const [isOwner, setIsOwner] = useState(false)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)

  const loadDetail = useCallback(async (reportId: string): Promise<ReportView> => {
    const payload = await fetchAPI<unknown>(`/admin/hospitals/${hospitalId}/reports/${reportId}`)
    const report = parseReport(payload)
    if (!report?.review) throw new Error('검수 근거가 없는 응답입니다.')
    return report
  }, [hospitalId])

  const applyReport = useCallback((report: ReportView) => {
    setSelected(report)
    setReports((current) => current.map((item) => item.id === report.id ? report : item))
  }, [])

  const openReport = useCallback(async (reportId: string) => {
    setLoadingId(reportId)
    setIssue(null)
    try { applyReport(await loadDetail(reportId)) }
    catch { setPageError('리포트 상세를 불러오지 못했습니다. 고객 영향: 최신 근거를 확인할 수 없습니다. 지금 할 일: 다시 시도하고 계속 실패하면 개발팀에 문의해 주세요.') }
    finally { setLoadingId(null) }
  }, [applyReport, loadDetail])

  useEffect(() => {
    let active = true
    Promise.all([
      fetchAPI<unknown>(`/admin/hospitals/${hospitalId}/reports`),
      fetchCurrentAccount(),
    ]).then(([payload, account]) => {
      if (!active) return
      setReports(parseReports(payload))
      setIsOwner(account?.role === 'OWNER')
      const reportId = new URLSearchParams(window.location.search).get('report')
      if (reportId) void openReport(reportId)
    }).catch(() => {
      if (active) setPageError('리포트 목록을 불러오지 못했습니다. 고객 영향: 검수와 전달 기록을 확인할 수 없습니다. 지금 할 일: 새로고침 후 계속 실패하면 개발팀에 병원명을 알려 주세요.')
    }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [hospitalId, openReport])

  const refreshSelected = useCallback(async () => {
    if (!selected) return
    setBusy(true)
    try { applyReport(await loadDetail(selected.id)); setIssue(null); setStatusMessage('최신 상태를 확인했습니다.') }
    catch { setIssue(LOAD_ERROR) }
    finally { setBusy(false) }
  }, [applyReport, loadDetail, selected])

  const handleAction = useCallback(async (action: DeliveryAction) => {
    if (!selected || busy) return
    setBusy(true)
    setIssue(null)
    try {
      const fresh = await loadDetail(selected.id)
      applyReport(fresh)
      const preflightIssue = preflightDeliveryAction(fresh, action.kind)
      if (preflightIssue) {
        setIssue(preflightIssue)
        return
      }
      const base = `/admin/hospitals/${hospitalId}/reports/${fresh.id}`
      const path = action.kind === 'deliver' ? `${base}/mark-sent` : action.kind === 'correct' ? `${base}/correct-delivery` : `${base}/rescind-delivery`
      const body = action.kind === 'rescind'
        ? { reason: action.reason }
        : {
            artifact_sha256: fresh.doctorArtifact.sha256,
            recipient_label: action.recipient,
            channel: action.channel,
            note: action.note,
            ...(action.kind === 'correct' ? { reason: action.reason } : {}),
          }
      const updated = parseReport(await fetchAPI<unknown>(path, { method: 'POST', body: JSON.stringify(body) }))
      if (!updated?.review) throw new Error('갱신된 검수 근거가 없습니다.')
      applyReport(updated)
      setStatusMessage(action.kind === 'deliver' ? '원장 전달 기록을 남겼습니다.' : action.kind === 'correct' ? '수정 기록을 덧붙였습니다.' : '전달 기록을 무효 처리했습니다. 이미 보낸 파일은 별도로 사용 중지를 안내해 주세요.')
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        try { applyReport(await loadDetail(selected.id)) } catch { /* conflict copy still remains actionable */ }
        setIssue(deliveryConflict(caught.detail))
      } else if (caught instanceof ApiError && caught.status === 403) {
        setIssue({ title: '이 작업을 실행할 권한이 없습니다', problem: '현재 계정은 이 전달 기록을 수정할 수 없습니다.', customerImpact: '전달 이력은 변경되지 않았습니다.', nextAction: '관리자에게 이 리포트 기간과 필요한 조치를 알려 주세요.', action: 'developer' })
      } else setIssue(LOAD_ERROR)
    } finally { setBusy(false) }
  }, [applyReport, busy, hospitalId, loadDetail, selected])

  const closeDialog = useCallback(() => { setSelected(null); setIssue(null) }, [])
  async function copyDeveloperInfo() {
    try {
      const note = selected
        ? deliveryDeveloperNote(hospitalId, selected.id, `${selected.periodYear}년 ${selected.periodMonth}월`)
        : reportListDeveloperNote(hospitalId)
      await navigator.clipboard.writeText(note)
      setStatusMessage('개발팀 문의용 정보를 복사했습니다.')
    } catch { setStatusMessage('복사하지 못했습니다. 개발팀에 병원명과 리포트 기간을 알려 주세요.') }
  }

  return (
    <div className="p-4 sm:p-6 lg:p-8">
      <header data-current-task className="mb-5"><h2 className="text-xl font-bold text-[var(--color-revisit-text-title)]">월간 리포트 검수와 전달</h2><p className="mt-1 text-sm leading-6 text-[var(--color-revisit-text-helper)] [word-break:keep-all]">측정 근거와 원장 전달용 파일을 먼저 확인하고, 같은 화면에서 전달 이력을 남깁니다.</p></header>
      <ReportRunStatus
        hospitalId={hospitalId}
        reportPeriods={reports.map((report) => ({
          periodYear: report.periodYear,
          periodMonth: report.periodMonth,
        }))}
        onReview={(reportId) => void openReport(reportId)}
      />
      {statusMessage && <p className="mb-4 rounded-lg bg-[var(--color-revisit-primary-95)] p-3 text-sm" role="status">{statusMessage}</p>}
      {pageError && <div className="mb-4 rounded-lg border border-[var(--color-revisit-red-50)] p-3 text-sm text-[var(--color-revisit-red-50)]" role="alert"><p>{pageError}</p><div className="mt-3 flex flex-col gap-2 sm:flex-row"><button type="button" onClick={() => window.location.reload()} className="min-h-11 rounded-lg bg-[var(--color-revisit-primary-40)] px-4 font-bold text-white">리포트 목록 다시 시도</button><button type="button" onClick={() => void copyDeveloperInfo()} className="min-h-11 rounded-lg border border-[var(--color-revisit-coolgrey-20)] px-4 font-bold text-[var(--color-revisit-text-title)]">개발팀 문의용 정보 복사</button></div></div>}
      {loading ? <p className="py-12 text-center text-sm" role="status">리포트 목록을 불러오는 중입니다.</p> : <ReportList reports={reports} loadingId={loadingId} onOpen={(report) => void openReport(report.id)} />}
      {selected && <ReportReviewDialog report={selected} issue={issue} isOwner={isOwner} busy={busy} onClose={closeDialog} onRefresh={() => void refreshSelected()} onAction={(action) => void handleAction(action)} onCopyIssue={() => void copyDeveloperInfo()} onCopyNotification={() => void copyDeveloperInfo()} />}
    </div>
  )
}
