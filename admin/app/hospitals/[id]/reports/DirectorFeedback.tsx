'use client'

import { useCallback, useEffect, useState } from 'react'
import { fetchAPI } from '@/lib/api'

type Feedback = { id: string; status: string; prefer_topics: string[]; prefer_messages: string[]; avoid_messages: string[] }

export function DirectorFeedback({ hospitalId }: { hospitalId: string }) {
  const [rows, setRows] = useState<Feedback[]>([])
  const [topics, setTopics] = useState('')
  const [messages, setMessages] = useState('')
  const [avoid, setAvoid] = useState('')
  const [reference, setReference] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const path = `/admin/hospitals/${hospitalId}/director-feedback`
  const load = useCallback(async () => setRows(await fetchAPI<Feedback[]>(`${path}?limit=100`)), [path])
  useEffect(() => { void load().catch(() => setStatus('대화 반영 기록을 불러오지 못했습니다.')) }, [load])
  const lines = (value: string) => value.split('\n').map((line) => line.trim()).filter(Boolean)
  async function save() {
    setBusy(true)
    try {
      await fetchAPI(path, { method: 'POST', body: JSON.stringify({
        source: 'DIRECTOR', prefer_topics: lines(topics), prefer_messages: lines(messages),
        avoid_messages: lines(avoid), conversation_reference: reference || null,
      }) })
      setTopics(''); setMessages(''); setAvoid(''); setReference('')
      setStatus('대화 내용을 저장했습니다. 이후 새로 생성하는 글부터 반영합니다.')
      try { await load() }
      catch { setStatus('저장은 완료됐지만 목록을 갱신하지 못했습니다. 새로고침해 주세요.') }
    } catch (error) { setStatus(error instanceof Error ? error.message : '저장하지 못했습니다. 입력 내용을 확인해 주세요.') }
    finally { setBusy(false) }
  }
  async function retire(id: string) {
    setBusy(true)
    try {
      await fetchAPI(`${path}/${id}/retire`, { method: 'POST' })
      setStatus('이 선호의 적용을 종료했습니다.')
      try { await load() }
      catch { setStatus('적용 종료는 완료됐지만 목록을 갱신하지 못했습니다. 새로고침해 주세요.') }
    }
    catch { setStatus('적용을 종료하지 못했습니다. 다시 시도해 주세요.') }
    finally { setBusy(false) }
  }
  return <section className="my-6 rounded-lg border p-4" aria-labelledby="director-feedback-title">
    <h3 id="director-feedback-title" className="font-bold">원장 대화 반영</h3>
    <p className="my-2 text-sm">관심 주제와 표현 선호를 다음 글에 반영합니다. 기존 글은 자동으로 다시 만들지 않습니다. 의료 사실은 병원 자료로 등록하며, 안전 기준이 항상 우선합니다.</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <label>관심 주제 (한 줄에 하나)<textarea disabled={busy} className="block w-full rounded border p-2" maxLength={10000} value={topics} onChange={(e) => setTopics(e.target.value)} /></label>
      <label>선호 표현 (한 줄에 하나)<textarea disabled={busy} className="block w-full rounded border p-2" maxLength={10000} value={messages} onChange={(e) => setMessages(e.target.value)} /></label>
      <label>피할 표현 (한 줄에 하나)<textarea disabled={busy} className="block w-full rounded border p-2" maxLength={10000} value={avoid} onChange={(e) => setAvoid(e.target.value)} /></label>
      <label>대화 날짜·보고서 참조<input disabled={busy} className="block w-full rounded border p-2" maxLength={500} value={reference} onChange={(e) => setReference(e.target.value)} /></label>
    </div>
    <button type="button" className="my-3 min-h-11 rounded border px-4" disabled={busy || ![topics, messages, avoid].some((v) => v.trim())} onClick={() => void save()}>대화 내용 저장</button>
    {status && <p role="status">{status}</p>}
    <ul>{rows.filter((row) => row.status === 'ACTIVE').map((row) => <li key={row.id} className="my-2 flex items-center justify-between gap-3 border-t pt-2">
      <span>{[...row.prefer_topics, ...row.prefer_messages, ...row.avoid_messages.map((v) => `피할 표현: ${v}`)].join(' · ')}</span>
      <button type="button" className="min-h-11 shrink-0 rounded border px-3" disabled={busy} onClick={() => void retire(row.id)}>적용 종료</button>
    </li>)}</ul>
  </section>
}
