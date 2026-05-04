'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'next/navigation'
import { fetchAPI } from '@/lib/api'
import {
  ContentPhilosophy,
  EvidenceNote,
  SourceAsset,
  SourceStatus,
  SourceType,
} from '@/types'

const SOURCE_TYPES: Array<{ value: SourceType; label: string }> = [
  { value: 'NAVER_BLOG', label: 'Naver Blog' },
  { value: 'YOUTUBE', label: 'YouTube' },
  { value: 'HOMEPAGE', label: 'Homepage' },
  { value: 'INTERVIEW', label: 'Interview' },
  { value: 'LANDING_PAGE', label: 'Landing Page' },
  { value: 'BROCHURE', label: 'Brochure' },
  { value: 'INTERNAL_NOTE', label: 'Internal Note' },
  { value: 'OTHER', label: 'Other' },
]

const SOURCE_STATUS_STYLE: Record<SourceStatus, { label: string; color: string }> = {
  PENDING: { label: '대기', color: 'bg-slate-100 text-slate-700' },
  PROCESSED: { label: '처리완료', color: 'bg-emerald-100 text-emerald-700' },
  EXCLUDED: { label: '제외', color: 'bg-red-50 text-red-600' },
  ERROR: { label: '오류', color: 'bg-orange-100 text-orange-700' },
}

function listToText(values: unknown): string {
  return Array.isArray(values) ? values.map((item) => String(item)).join('\n') : ''
}

function textToList(value: string): string[] {
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

function formatDate(value: string | null): string {
  return value ? new Date(value).toLocaleString('ko-KR') : '-'
}

function statusBadge(status: string) {
  if (status === 'APPROVED') return 'bg-emerald-100 text-emerald-700'
  if (status === 'DRAFT') return 'bg-blue-100 text-blue-700'
  return 'bg-slate-100 text-slate-700'
}

export default function EssencePage() {
  const { id } = useParams<{ id: string }>()

  const [sources, setSources] = useState<SourceAsset[]>([])
  const [selectedSource, setSelectedSource] = useState<SourceAsset | null>(null)
  const [philosophies, setPhilosophies] = useState<ContentPhilosophy[]>([])
  const [approved, setApproved] = useState<ContentPhilosophy | null>(null)
  const [selectedSourceIds, setSelectedSourceIds] = useState<Set<string>>(new Set())
  const [selectedDraftId, setSelectedDraftId] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [sourceType, setSourceType] = useState<SourceType>('INTERVIEW')
  const [sourceTitle, setSourceTitle] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [sourceRawText, setSourceRawText] = useState('')
  const [sourceOperatorNote, setSourceOperatorNote] = useState('')
  const [sourceCreatedBy, setSourceCreatedBy] = useState('MotionLabs')

  const [draftPositioning, setDraftPositioning] = useState('')
  const [draftVoice, setDraftVoice] = useState('')
  const [draftPromise, setDraftPromise] = useState('')
  const [draftPrinciples, setDraftPrinciples] = useState('')
  const [draftTone, setDraftTone] = useState('')
  const [draftMustUse, setDraftMustUse] = useState('')
  const [draftAvoid, setDraftAvoid] = useState('')
  const [draftRiskRules, setDraftRiskRules] = useState('')
  const [reviewedBy, setReviewedBy] = useState('MotionLabs')
  const [approvalNote, setApprovalNote] = useState('')
  const [confirmEvidence, setConfirmEvidence] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [sourceData, philosophyData, approvedData] = await Promise.all([
        fetchAPI(`/admin/hospitals/${id}/essence/sources`),
        fetchAPI(`/admin/hospitals/${id}/essence/philosophies`),
        fetchAPI(`/admin/hospitals/${id}/essence/philosophy/approved`),
      ])
      setSources(Array.isArray(sourceData) ? sourceData : [])
      setPhilosophies(Array.isArray(philosophyData) ? philosophyData : [])
      setApproved(approvedData?.approved ?? null)
      const processedIds = (Array.isArray(sourceData) ? sourceData : [])
        .filter((source: SourceAsset) => source.status === 'PROCESSED')
        .map((source: SourceAsset) => source.id)
      setSelectedSourceIds(new Set(processedIds))
      const latestDraft = (Array.isArray(philosophyData) ? philosophyData : []).find(
        (item: ContentPhilosophy) => item.status === 'DRAFT'
      )
      setSelectedDraftId(latestDraft?.id ?? null)
      if (latestDraft) setDraftFields(latestDraft)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '콘텐츠 운영 기준 데이터를 불러오지 못했습니다.')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    load()
  }, [load])

  const processedSources = useMemo(
    () => sources.filter((source) => source.status === 'PROCESSED'),
    [sources]
  )
  const selectedDraft = useMemo(
    () => philosophies.find((item) => item.id === selectedDraftId) ?? null,
    [philosophies, selectedDraftId]
  )
  const draftCount = philosophies.filter((item) => item.status === 'DRAFT').length
  const evidenceTotal = sources.reduce((sum, item) => sum + (item.evidence_note_count ?? 0), 0)

  // Operator next-action hint
  const nextAction = useMemo(() => {
    if (sources.length === 0) return '① 자료를 1개 이상 입력하세요.'
    if (processedSources.length === 0) return '② 자료의 [근거 추출]을 실행하세요.'
    if (!selectedDraft && !approved) return '③ 처리된 자료를 선택하고 [선택 자료로 초안 만들기]를 누르세요.'
    if (selectedDraft && selectedDraft.status === 'DRAFT')
      return '④ 초안 내용을 검토 후 우측에서 [승인]하세요.'
    if (approved) return '운영 중 — 새 자료를 추가하면 새 버전 초안을 만들 수 있습니다.'
    return null
  }, [sources.length, processedSources.length, selectedDraft, approved])

  function setDraftFields(philosophy: ContentPhilosophy) {
    setDraftPositioning(philosophy.positioning_statement ?? '')
    setDraftVoice(philosophy.doctor_voice ?? '')
    setDraftPromise(philosophy.patient_promise ?? '')
    setDraftPrinciples(listToText(philosophy.content_principles))
    setDraftTone(listToText(philosophy.tone_guidelines))
    setDraftMustUse(listToText(philosophy.must_use_messages))
    setDraftAvoid(listToText(philosophy.avoid_messages))
    setDraftRiskRules(listToText(philosophy.medical_ad_risk_rules))
  }

  async function createSource(e: React.FormEvent) {
    e.preventDefault()
    setActionLoading('create-source')
    setError(null)
    setNotice(null)
    try {
      await fetchAPI(`/admin/hospitals/${id}/essence/sources`, {
        method: 'POST',
        body: JSON.stringify({
          source_type: sourceType,
          title: sourceTitle,
          url: sourceUrl || null,
          raw_text: sourceRawText || null,
          operator_note: sourceOperatorNote || null,
          source_metadata: {},
          created_by: sourceCreatedBy || null,
        }),
      })
      setSourceTitle('')
      setSourceUrl('')
      setSourceRawText('')
      setSourceOperatorNote('')
      setNotice('자료가 저장되었습니다. [근거 추출]을 실행해 다음 단계로 진행하세요.')
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '자료 저장에 실패했습니다.')
    } finally {
      setActionLoading(null)
    }
  }

  async function processSource(sourceId: string) {
    setActionLoading(`process-${sourceId}`)
    setError(null)
    setNotice(null)
    try {
      const detail = await fetchAPI(`/admin/hospitals/${id}/essence/sources/${sourceId}/process`, {
        method: 'POST',
      })
      setSelectedSource(detail)
      setNotice('근거 추출이 완료되었습니다.')
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '자료 처리에 실패했습니다.')
    } finally {
      setActionLoading(null)
    }
  }

  async function openSource(sourceId: string) {
    setActionLoading(`open-${sourceId}`)
    setError(null)
    try {
      const detail = await fetchAPI(`/admin/hospitals/${id}/essence/sources/${sourceId}`)
      setSelectedSource(detail)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '자료 상세를 불러오지 못했습니다.')
    } finally {
      setActionLoading(null)
    }
  }

  async function excludeSource(sourceId: string) {
    if (!confirm('이 자료를 제외하시겠습니까?')) return
    setActionLoading(`exclude-${sourceId}`)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${id}/essence/sources/${sourceId}/exclude`, { method: 'POST' })
      setSelectedSource(null)
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '자료 제외에 실패했습니다.')
    } finally {
      setActionLoading(null)
    }
  }

  async function createDraft() {
    const sourceIds = Array.from(selectedSourceIds)
    if (sourceIds.length === 0) return
    setActionLoading('create-draft')
    setError(null)
    setNotice(null)
    try {
      const draft = await fetchAPI(`/admin/hospitals/${id}/essence/philosophy/draft`, {
        method: 'POST',
        body: JSON.stringify({
          source_asset_ids: sourceIds,
          created_by: 'MotionLabs',
        }),
      })
      setSelectedDraftId(draft.id)
      setDraftFields(draft)
      setNotice('콘텐츠 운영 기준 초안이 생성되었습니다. 내용을 검토 후 승인하세요.')
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '초안 생성에 실패했습니다.')
    } finally {
      setActionLoading(null)
    }
  }

  async function saveDraft() {
    if (!selectedDraft || selectedDraft.status !== 'DRAFT') return
    setActionLoading('save-draft')
    setError(null)
    setNotice(null)
    try {
      const updated = await fetchAPI(`/admin/hospitals/${id}/essence/philosophy/${selectedDraft.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          positioning_statement: draftPositioning || null,
          doctor_voice: draftVoice || null,
          patient_promise: draftPromise || null,
          content_principles: textToList(draftPrinciples),
          tone_guidelines: textToList(draftTone),
          must_use_messages: textToList(draftMustUse),
          avoid_messages: textToList(draftAvoid),
          medical_ad_risk_rules: textToList(draftRiskRules),
        }),
      })
      setDraftFields(updated)
      setNotice('초안이 저장되었습니다.')
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '초안 저장에 실패했습니다.')
    } finally {
      setActionLoading(null)
    }
  }

  async function approveDraft() {
    if (!selectedDraft || selectedDraft.status !== 'DRAFT') return
    setActionLoading('approve-draft')
    setError(null)
    setNotice(null)
    try {
      await fetchAPI(`/admin/hospitals/${id}/essence/philosophy/${selectedDraft.id}/approve`, {
        method: 'POST',
        body: JSON.stringify({
          reviewed_by: reviewedBy,
          approval_note: approvalNote || null,
          confirm_evidence_reviewed: confirmEvidence,
        }),
      })
      setApprovalNote('')
      setConfirmEvidence(false)
      setNotice('콘텐츠 운영 기준이 승인되었습니다. 자동 콘텐츠 생성에 적용됩니다.')
      await load()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '승인에 실패했습니다.')
    } finally {
      setActionLoading(null)
    }
  }

  function toggleSource(sourceId: string) {
    setSelectedSourceIds((prev) => {
      const next = new Set(prev)
      if (next.has(sourceId)) next.delete(sourceId)
      else next.add(sourceId)
      return next
    })
  }

  if (loading) {
    return <div className="p-8 text-slate-500">불러오는 중...</div>
  }

  return (
    <div className="p-8 space-y-6 max-w-[1400px] mx-auto">
      {/* Page header */}
      <div className="flex items-start justify-between gap-6 flex-wrap">
        <div className="min-w-0">
          <h2 className="text-xl font-bold text-slate-900">콘텐츠 운영 기준</h2>
          <p className="text-sm text-slate-500 mt-1 max-w-2xl">
            병원 자료에서 AI가 참고할 근거를 뽑고, 콘텐츠가 지켜야 할 말투와 메시지를 운영 기준으로 고정합니다.
            승인 전에는 자동 콘텐츠가 발행 차단됩니다.
          </p>
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 min-w-[480px]">
          <SummaryCard
            label="승인된 운영 기준"
            value={approved ? `v${approved.version} 운영 중` : '미승인'}
            tone={approved ? 'good' : 'warn'}
          />
          <SummaryCard
            label="처리된 자료"
            value={`${processedSources.length} / ${sources.length}`}
            hint="처리완료 / 전체"
          />
          <SummaryCard
            label="근거 노트"
            value={`${evidenceTotal}개`}
            hint="전체 자료 합계"
          />
          <SummaryCard
            label="검토 대기 초안"
            value={`${draftCount}개`}
            tone={draftCount > 0 ? 'warn' : undefined}
          />
        </div>
      </div>

      {/* Operator hint banner */}
      {nextAction && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg px-4 py-3 text-sm text-blue-900 flex items-start gap-3">
          <span
            className="shrink-0 inline-flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-[11px] font-bold"
            aria-hidden
          >
            i
          </span>
          <span>
            <span className="font-semibold">다음 작업:</span> <span>{nextAction}</span>
          </span>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-700 text-sm">{error}</div>
      )}
      {notice && (
        <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4 text-emerald-800 text-sm">{notice}</div>
      )}

      {/* Active approved philosophy (sticky priority above workflow) */}
      {approved && (
        <section className="bg-white rounded-xl border border-emerald-200 p-5">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <span className="inline-flex px-2 py-0.5 rounded-full text-[11px] font-bold bg-emerald-600 text-white">
                  운영 기준
                </span>
                <p className="text-base font-semibold text-slate-900">
                  콘텐츠 운영 기준 v{approved.version} 운영 중
                </p>
              </div>
              <p className="text-xs text-slate-500">
                승인일 {formatDate(approved.approved_at)} · 검토자 {approved.reviewed_by ?? '-'} · 연결된 자료 {approved.source_asset_ids.length}개
              </p>
            </div>
            <p className="text-xs text-slate-600 max-w-md">
              자동 생성되는 콘텐츠는 이 운영 기준을 통과해야 발행됩니다. 새 자료를 추가하면 신규 초안을 만들어 갱신하세요.
            </p>
          </div>
        </section>
      )}

      {/* STEP 1 + 2: Sources */}
      <div className="grid xl:grid-cols-[400px_1fr] gap-6">
        {/* Step 1 — input source */}
        <form onSubmit={createSource} className="bg-white rounded-xl border border-slate-200 p-6 space-y-4 self-start">
          <header>
            <StepLabel index={1} label="자료 입력" />
            <p className="text-xs text-slate-500 mt-1.5">
              인터뷰·블로그·기존 홈페이지 등 원장 목소리가 담긴 자료를 입력합니다. AI가 참고할 근거를 뽑으려면 원문 텍스트를 붙여넣어야 합니다.
            </p>
          </header>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">자료 유형</label>
            <select
              value={sourceType}
              onChange={(e) => setSourceType(e.target.value as SourceType)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {SOURCE_TYPES.map((type) => (
                <option key={type.value} value={type.value}>{type.label}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">제목</label>
            <input
              value={sourceTitle}
              onChange={(e) => setSourceTitle(e.target.value)}
              required
              placeholder="예: 원장 인터뷰 — 진료 철학"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">URL <span className="text-slate-400 text-xs font-normal">(선택)</span></label>
            <input
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
              placeholder="https://..."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">
              원문 <span className="text-slate-400 text-xs font-normal">(처리에 필수)</span>
            </label>
            <textarea
              value={sourceRawText}
              onChange={(e) => setSourceRawText(e.target.value)}
              rows={8}
              placeholder="원문 텍스트를 그대로 붙여넣으세요. 근거 노트 추출 대상이 됩니다."
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">운영자 메모 <span className="text-slate-400 text-xs font-normal">(선택)</span></label>
            <textarea
              value={sourceOperatorNote}
              onChange={(e) => setSourceOperatorNote(e.target.value)}
              rows={3}
              placeholder="이 자료에 대한 컨텍스트, 출처, 주의사항 등"
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-slate-700 mb-1.5">작성자</label>
            <input
              value={sourceCreatedBy}
              onChange={(e) => setSourceCreatedBy(e.target.value)}
              className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>
          <button
            type="submit"
            disabled={actionLoading === 'create-source'}
            className="w-full py-2.5 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {actionLoading === 'create-source' ? '저장 중...' : '자료 저장'}
          </button>
        </form>

        {/* Step 2 — process & select sources */}
        <section className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-slate-100 flex items-start justify-between gap-4 flex-wrap">
            <div className="min-w-0">
              <StepLabel index={2} label="근거 추출 · 초안 만들기" />
              <p className="text-xs text-slate-500 mt-1.5">
                각 자료의 [근거 추출]을 실행한 뒤, 사용할 자료를 체크해 콘텐츠 운영 기준 초안을 만듭니다.
              </p>
            </div>
            <button
              onClick={createDraft}
              disabled={selectedSourceIds.size === 0 || actionLoading === 'create-draft'}
              className="shrink-0 whitespace-nowrap px-4 py-2 bg-emerald-600 text-white text-sm font-semibold rounded-lg hover:bg-emerald-700 disabled:opacity-50"
              title={selectedSourceIds.size === 0 ? '먼저 처리완료된 자료를 선택하세요.' : ''}
            >
              {actionLoading === 'create-draft'
                ? '생성 중...'
                : `선택한 ${selectedSourceIds.size}개 자료로 초안 만들기`}
            </button>
          </div>
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="px-4 py-3 w-10"></th>
                <th className="text-left px-4 py-3 text-slate-600 font-medium">자료</th>
                <th className="text-center px-4 py-3 text-slate-600 font-medium whitespace-nowrap">상태</th>
                <th className="text-center px-4 py-3 text-slate-600 font-medium whitespace-nowrap">근거</th>
                <th className="text-right px-4 py-3 text-slate-600 font-medium whitespace-nowrap">액션</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {sources.length === 0 && (
                <tr>
                  <td colSpan={5} className="py-16 text-center text-slate-400 text-sm">
                    아직 등록된 자료가 없습니다. 좌측에서 자료를 입력하세요.
                  </td>
                </tr>
              )}
              {sources.map((source) => {
                const statusStyle = SOURCE_STATUS_STYLE[source.status]
                return (
                  <tr key={source.id} className="hover:bg-slate-50/70">
                    <td className="px-4 py-4 align-top">
                      <input
                        type="checkbox"
                        checked={selectedSourceIds.has(source.id)}
                        onChange={() => toggleSource(source.id)}
                        disabled={source.status !== 'PROCESSED'}
                        title={source.status !== 'PROCESSED' ? '처리완료된 자료만 선택할 수 있습니다.' : ''}
                        className="w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500 disabled:opacity-30"
                      />
                    </td>
                    <td className="px-4 py-4">
                      <p className="font-medium text-slate-900">{source.title}</p>
                      <p className="text-xs text-slate-500 mt-0.5">
                        <span className="inline-block px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 mr-1.5">
                          {source.source_type}
                        </span>
                        {source.url ? (
                          <a
                            href={source.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-blue-600 hover:underline truncate inline-block max-w-[260px] align-bottom"
                          >
                            {source.url}
                          </a>
                        ) : (
                          <span className="text-slate-400">URL 없음</span>
                        )}
                      </p>
                      {source.process_error && (
                        <p className="text-xs text-red-600 mt-1">{source.process_error}</p>
                      )}
                    </td>
                    <td className="px-4 py-4 text-center align-top">
                      <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-medium ${statusStyle.color}`}>
                        {statusStyle.label}
                      </span>
                    </td>
                    <td className="px-4 py-4 text-center align-top text-slate-700 font-medium">
                      {source.evidence_note_count}
                    </td>
                    <td className="px-4 py-4 align-top">
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          onClick={() => openSource(source.id)}
                          className="px-2.5 py-1 bg-slate-50 text-slate-700 text-xs rounded hover:bg-slate-100 border border-slate-200"
                          title="근거 노트를 확인합니다."
                        >
                          근거 보기
                        </button>
                        <button
                          onClick={() => processSource(source.id)}
                          disabled={actionLoading === `process-${source.id}` || source.status === 'EXCLUDED'}
                          className="px-2.5 py-1 bg-blue-50 text-blue-700 text-xs rounded hover:bg-blue-100 border border-blue-200 disabled:opacity-50"
                          title="원문에서 근거 노트를 추출합니다."
                        >
                          {actionLoading === `process-${source.id}`
                            ? '처리중...'
                            : source.status === 'PROCESSED'
                              ? '재처리'
                              : '근거 추출'}
                        </button>
                        <button
                          onClick={() => excludeSource(source.id)}
                          disabled={actionLoading === `exclude-${source.id}`}
                          className="px-2.5 py-1 bg-red-50 text-red-700 text-xs rounded hover:bg-red-100 border border-red-200 disabled:opacity-50"
                          title="향후 초안 생성 대상에서 제외합니다."
                        >
                          제외
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </section>
      </div>

      {selectedSource && (
        <section className="bg-white rounded-xl border border-slate-200 p-6">
          <div className="flex items-start justify-between gap-4 mb-4">
            <div>
              <h3 className="text-base font-semibold text-slate-900">{selectedSource.title}</h3>
              <p className="text-xs text-slate-500 mt-1">
                {selectedSource.source_type} · 처리일 {formatDate(selectedSource.processed_at)}
              </p>
            </div>
            <button
              onClick={() => setSelectedSource(null)}
              className="text-slate-400 hover:text-slate-600 text-xl"
              aria-label="닫기"
            >×</button>
          </div>
          <EvidenceList notes={selectedSource.evidence_notes ?? []} />
        </section>
      )}

      {/* STEP 3 — Philosophy review & approve */}
      <section className="bg-white rounded-xl border border-slate-200 p-6 space-y-5">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <StepLabel index={3} label="콘텐츠 운영 기준 검토 및 승인" />
            <p className="text-xs text-slate-500 mt-1.5">
              초안 상태만 편집할 수 있습니다. 승인하려면 각 메시지가 어떤 자료에서 나왔는지 확인되어야 합니다.
            </p>
          </div>
          {philosophies.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-slate-500 mr-1">버전:</span>
              {philosophies.map((item) => (
                <button
                  key={item.id}
                  onClick={() => { setSelectedDraftId(item.id); setDraftFields(item) }}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                    selectedDraftId === item.id
                      ? 'border-blue-500 text-blue-700 bg-blue-50'
                      : 'border-slate-200 text-slate-600 hover:border-slate-300'
                  }`}
                  title={`${item.status} · ${formatDate(item.created_at)}`}
                >
                  v{item.version}
                  <span className="ml-1.5 text-[10px] uppercase opacity-70">{item.status}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {!selectedDraft && approved && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-5 text-sm text-blue-900 space-y-2">
            <p className="font-semibold">검토 대기 중인 초안이 없습니다.</p>
            <p>
              승인된 v{approved.version} 철학이 자동 생성 기준으로 사용됩니다. 신규 자료를 처리한 뒤 위에서 “선택한 N개로 초안 만들기”를 눌러 새 버전을 만들 수 있습니다.
            </p>
          </div>
        )}

        {!selectedDraft && !approved && (
          <div className="bg-slate-50 border border-dashed border-slate-300 rounded-lg p-10 text-center text-slate-500 space-y-2">
            <p className="text-sm font-medium text-slate-600">승인된 콘텐츠 운영 기준이 없습니다.</p>
            <p className="text-xs text-slate-500 max-w-md mx-auto leading-relaxed">
              자료를 1개 이상 입력 → 근거 추출 → “선택 자료로 초안 만들기” 순서로 진행하세요.
              승인 전에는 자동 콘텐츠가 발행 차단됩니다.
            </p>
          </div>
        )}

        {selectedDraft && (
          <div className="grid xl:grid-cols-[1fr_340px] gap-6">
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <span className={`inline-flex px-2.5 py-0.5 rounded-full text-xs font-semibold ${statusBadge(selectedDraft.status)}`}>
                  {selectedDraft.status}
                </span>
                <span className="text-sm text-slate-500">version {selectedDraft.version}</span>
              </div>
              <TextArea label="Positioning Statement" value={draftPositioning} onChange={setDraftPositioning} disabled={selectedDraft.status !== 'DRAFT'} rows={3} />
              <TextArea label="Doctor Voice" value={draftVoice} onChange={setDraftVoice} disabled={selectedDraft.status !== 'DRAFT'} rows={3} />
              <TextArea label="Patient Promise" value={draftPromise} onChange={setDraftPromise} disabled={selectedDraft.status !== 'DRAFT'} rows={3} />
              <div className="grid md:grid-cols-2 gap-4">
                <TextArea label="Content Principles" value={draftPrinciples} onChange={setDraftPrinciples} disabled={selectedDraft.status !== 'DRAFT'} rows={6} hint="한 줄에 하나씩" />
                <TextArea label="Tone Guidelines" value={draftTone} onChange={setDraftTone} disabled={selectedDraft.status !== 'DRAFT'} rows={6} hint="한 줄에 하나씩" />
                <TextArea label="Must-use Messages" value={draftMustUse} onChange={setDraftMustUse} disabled={selectedDraft.status !== 'DRAFT'} rows={6} hint="한 줄에 하나씩" />
                <TextArea label="Avoid Messages" value={draftAvoid} onChange={setDraftAvoid} disabled={selectedDraft.status !== 'DRAFT'} rows={6} hint="한 줄에 하나씩" />
              </div>
              <TextArea label="Medical Ad Risk Rules" value={draftRiskRules} onChange={setDraftRiskRules} disabled={selectedDraft.status !== 'DRAFT'} rows={4} hint="의료광고법 관련 추가 운영 규칙 (한 줄에 하나씩)" />
              {selectedDraft.status === 'DRAFT' && (
                <div className="flex gap-3">
                  <button
                    onClick={saveDraft}
                    disabled={actionLoading === 'save-draft'}
                    className="px-5 py-2.5 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700 disabled:opacity-50"
                  >
                    {actionLoading === 'save-draft' ? '저장 중...' : '초안 저장'}
                  </button>
                </div>
              )}
            </div>

            <aside className="space-y-4">
              <div className="bg-slate-50 border border-slate-200 rounded-lg p-4">
                <p className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Evidence Map</p>
                <pre className="text-xs text-slate-600 whitespace-pre-wrap break-words max-h-48 overflow-auto">
                  {JSON.stringify(selectedDraft.evidence_map, null, 2)}
                </pre>
              </div>
              <div className="bg-slate-50 border border-slate-200 rounded-lg p-4">
                <p className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider mb-2">Unsupported Gaps</p>
                <pre className="text-xs text-slate-600 whitespace-pre-wrap break-words max-h-48 overflow-auto">
                  {JSON.stringify(selectedDraft.unsupported_gaps, null, 2)}
                </pre>
              </div>
              {selectedDraft.status === 'DRAFT' && (
                <div className="bg-white border border-emerald-200 rounded-lg p-4 space-y-3">
                  <p className="text-sm font-semibold text-slate-900">승인</p>
                  <p className="text-[11px] text-slate-500 leading-relaxed">
                    승인 전 체크: ① evidence_map의 모든 ID가 실제 근거 note인지 ② must_use / avoid 메시지가 의료광고 금지 표현과 충돌하지 않는지 ③ unsupported_gaps의 빈 칸이 운영 기준에 허용되는지.
                  </p>
                  <div>
                    <label className="block text-xs font-medium text-slate-600 mb-1">검토자</label>
                    <input
                      value={reviewedBy}
                      onChange={(e) => setReviewedBy(e.target.value)}
                      className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-600 mb-1">승인 메모</label>
                    <textarea
                      value={approvalNote}
                      onChange={(e) => setApprovalNote(e.target.value)}
                      rows={3}
                      className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
                    />
                  </div>
                  <label className="flex items-start gap-2 text-xs text-slate-600">
                    <input
                      type="checkbox"
                      checked={confirmEvidence}
                      onChange={(e) => setConfirmEvidence(e.target.checked)}
                      className="mt-0.5 w-4 h-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                    />
                    근거 노트와 원문 발췌를 검토했습니다.
                  </label>
                  <button
                    onClick={approveDraft}
                    disabled={!confirmEvidence || !reviewedBy.trim() || actionLoading === 'approve-draft'}
                    className="w-full py-2.5 bg-emerald-600 text-white text-sm font-semibold rounded-lg hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {actionLoading === 'approve-draft' ? '승인 중...' : '승인'}
                  </button>
                </div>
              )}
            </aside>
          </div>
        )}
      </section>
    </div>
  )
}

function StepLabel({ index, label }: { index: number; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span
        className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-slate-900 text-white text-[11px] font-bold"
        aria-hidden
      >
        {index}
      </span>
      <h3 className="text-base font-semibold text-slate-900">{label}</h3>
    </div>
  )
}

function SummaryCard({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: string
  hint?: string
  tone?: 'good' | 'warn'
}) {
  const valueClass =
    tone === 'good' ? 'text-emerald-700' : tone === 'warn' ? 'text-amber-700' : 'text-slate-900'
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className={`text-lg font-bold ${valueClass}`}>{value}</p>
      {hint && <p className="text-[11px] text-slate-400 mt-1">{hint}</p>}
    </div>
  )
}

function TextArea({
  label,
  value,
  onChange,
  disabled,
  rows,
  hint,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  rows: number
  hint?: string
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <label className="block text-sm font-medium text-slate-700">{label}</label>
        {hint && <span className="text-[11px] text-slate-400">{hint}</span>}
      </div>
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        rows={rows}
        className="w-full px-3 py-2 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none disabled:bg-slate-50 disabled:text-slate-500"
      />
    </div>
  )
}

function EvidenceList({ notes }: { notes: EvidenceNote[] }) {
  if (notes.length === 0) {
    return <div className="bg-slate-50 rounded-lg p-8 text-center text-slate-400 text-sm">추출된 evidence note가 없습니다.</div>
  }
  return (
    <div className="grid md:grid-cols-2 gap-3">
      {notes.map((note) => (
        <div key={note.id} className="border border-slate-200 rounded-lg p-4 bg-slate-50/40">
          <div className="flex items-center justify-between gap-3 mb-2">
            <span className="text-xs font-semibold text-blue-700 uppercase tracking-wide">{note.note_type}</span>
            <span className="text-xs text-slate-400">신뢰도 {note.confidence ? Math.round(note.confidence * 100) : '-'}%</span>
          </div>
          <p className="text-sm font-medium text-slate-800">{note.claim}</p>
          <p className="text-xs text-slate-500 mt-2 leading-relaxed">{note.source_excerpt}</p>
        </div>
      ))}
    </div>
  )
}
