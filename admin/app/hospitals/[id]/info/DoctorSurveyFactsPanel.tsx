'use client'

import { useEffect, useState } from 'react'
import { ADMIN_COPY } from '@/lib/admin-copy'
import { mergeFactPatches, type DoctorSurveyFact } from '@/lib/doctor-survey'
import { fetchAPI } from '@/lib/api'

/**
 * 원장님이 제출한 사실 답(병원명·의료진·진료 항목·주소·전화·진료시간·채널)을 AE가 보고 병원 정보에 반영한다.
 * 빈 칸만 고를 수 있다. 반영은 기존 프로필 저장으로 보내고, 저장하지 않은 병원 정보 폼이 옛 값으로 덮지 않게
 * 화면을 다시 불러온다.
 */
export default function DoctorSurveyFactsPanel({ hospitalId }: { hospitalId: string }) {
  const copy = ADMIN_COPY.doctorSurvey
  const [facts, setFacts] = useState<DoctorSurveyFact[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchAPI<{ items: DoctorSurveyFact[] }>(`/admin/hospitals/${hospitalId}/doctor-survey/facts`)
      .then((data) => {
        if (cancelled) return
        setFacts(data.items)
        setSelected(new Set(data.items.filter((item) => item.applicable).map((item) => item.field)))
      })
      .catch(() => !cancelled && setFeedback(copy.factsLoadFailed))
    return () => {
      cancelled = true
    }
  }, [hospitalId, copy.factsLoadFailed])

  if (facts === null) return feedback ? <p className="text-xs text-slate-600">{feedback}</p> : null
  if (facts.length === 0) return null

  async function apply() {
    if (!facts) return
    const body = mergeFactPatches(facts, selected)
    if (Object.keys(body).length === 0) return
    if (!window.confirm(copy.factsApplyConfirm)) return
    setBusy(true)
    setFeedback(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/profile`, { method: 'PATCH', body: JSON.stringify(body) })
      setFeedback(copy.factsApplied)
      window.location.reload()
    } catch {
      setFeedback(copy.factsFailed)
      setBusy(false)
    }
  }

  const toggle = (field: string) =>
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(field)) next.delete(field)
      else next.add(field)
      return next
    })
  const anySelected = facts.some((item) => item.applicable && selected.has(item.field))

  return (
    <div data-testid="doctor-survey-facts" className="space-y-2 rounded-lg border border-slate-200 bg-white p-3">
      <div>
        <h5 className="text-sm font-semibold text-slate-800">{copy.factsTitle}</h5>
        <p className="mt-0.5 text-xs leading-5 text-slate-600">{copy.factsHint}</p>
      </div>
      <ul className="space-y-1.5">
        {facts.map((item) => (
          <li key={item.field} className="text-xs text-slate-700">
            <label className={`flex gap-2 ${item.applicable ? 'cursor-pointer' : ''}`}>
              <input
                type="checkbox"
                className="mt-0.5"
                disabled={!item.applicable || busy}
                checked={item.applicable && selected.has(item.field)}
                onChange={() => toggle(item.field)}
              />
              <span className="min-w-0">
                <span className="font-semibold">{item.label}</span>
                <span className="ml-1.5 break-all">{item.value}</span>
                {item.current ? (
                  <span className="block text-slate-500">{copy.factsCurrent}: {item.current}</span>
                ) : null}
                {item.reason ? <span className="block text-amber-800">{item.reason}</span> : null}
              </span>
            </label>
          </li>
        ))}
      </ul>
      <button
        type="button"
        disabled={busy || !anySelected}
        onClick={() => void apply()}
        className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {copy.factsApply}
      </button>
      {feedback ? <p role="status" className="text-xs text-slate-600">{feedback}</p> : null}
    </div>
  )
}
