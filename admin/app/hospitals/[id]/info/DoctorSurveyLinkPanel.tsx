'use client'

import { useCallback, useEffect, useState } from 'react'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { doctorSurveyStatusText, type DoctorSurveyStatus } from '@/lib/doctor-survey'
import { fetchAPI } from '@/lib/api'
import { formatDate } from '@/lib/format'

/**
 * 원장님이 직접 작성하는 사전 설문 링크 — 만들기·복사·새 링크와 진행 상태.
 * 링크는 AE가 복사해 보낸다(시스템은 보내지 않는다). 제출해도 알림은 없고 상태는 여기서 본다.
 */
export default function DoctorSurveyLinkPanel({ hospitalId }: { hospitalId: string }) {
  const copy = ADMIN_COPY.doctorSurvey
  const [survey, setSurvey] = useState<DoctorSurveyStatus | null>(null)
  const [newClinic, setNewClinic] = useState(false)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await fetchAPI<DoctorSurveyStatus>(`/admin/hospitals/${hospitalId}/doctor-survey`)
      setSurvey(data)
      setNewClinic(Boolean(data.new_clinic))
    } catch {
      setFeedback(copy.loadFailed)
    }
  }, [hospitalId, copy.loadFailed])

  useEffect(() => {
    void load()
  }, [load])

  async function issue(reissue: boolean, nextNewClinic = newClinic) {
    setBusy(true)
    setFeedback(null)
    try {
      const data = await fetchAPI<DoctorSurveyStatus>(`/admin/hospitals/${hospitalId}/doctor-survey/link`, {
        method: 'POST',
        body: JSON.stringify({ new_clinic: nextNewClinic, reissue }),
      })
      setSurvey(data)
      setNewClinic(Boolean(data.new_clinic))
      if (reissue) setFeedback(copy.reissued)
    } catch {
      setFeedback(copy.saveFailed)
    } finally {
      setBusy(false)
    }
  }

  async function copyLink() {
    if (!survey?.link_url) return
    try {
      await navigator.clipboard.writeText(survey.link_url)
      setFeedback(copy.copied)
    } catch {
      setFeedback(copy.copyFailed)
    }
  }

  const issued = Boolean(survey?.link_url)
  return (
    <div data-testid="doctor-survey-link" className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
      <div>
        <h5 className="text-sm font-semibold text-slate-800">{copy.title}</h5>
        <p className="mt-0.5 text-xs leading-5 text-slate-600">{copy.hint}</p>
      </div>

      <label className="flex items-center gap-2 text-xs text-slate-700">
        <input
          type="checkbox"
          checked={newClinic}
          disabled={busy || survey === null}
          onChange={(e) => {
            setNewClinic(e.target.checked)
            if (issued) void issue(false, e.target.checked)
          }}
        />
        {copy.newClinic}
      </label>

      {issued ? (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <input
              readOnly
              value={survey?.link_url ?? ''}
              aria-label={copy.title}
              onFocus={(e) => e.currentTarget.select()}
              className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-700"
            />
            <button
              type="button"
              onClick={() => void copyLink()}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
            >
              {copy.copy}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (window.confirm(copy.reissueConfirm)) void issue(true)
              }}
              className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              {copy.reissue}
            </button>
          </div>
          <p className="text-xs text-slate-600">
            {survey ? doctorSurveyStatusText(survey) : ''}
            {survey?.respondent === 'STAFF' ? ` · ${copy.respondentStaff}` : ''}
            {survey?.link_issued_at ? ` · ${copy.issuedAt} ${formatDate(survey.link_issued_at)}` : ''}
          </p>
          {survey?.has_unsubmitted_changes ? <p className="text-xs text-amber-800">{copy.statusEdited}</p> : null}
        </>
      ) : (
        <button
          type="button"
          disabled={busy || survey === null}
          onClick={() => void issue(false)}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {copy.create}
        </button>
      )}

      {feedback ? <p role="status" className="text-xs text-slate-600">{feedback}</p> : null}
    </div>
  )
}
