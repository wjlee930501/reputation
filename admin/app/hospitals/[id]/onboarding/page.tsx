'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { fetchAPI } from '@/lib/api'

interface Hospital {
  id: string
  name: string
  slug: string
  profile_complete: boolean
  v0_report_done: boolean
  schedule_set: boolean
  status: string
}

interface Source {
  id: string
  source_type: string
  title: string
  status: string
  url: string | null
  file_url: string | null
  file_access_url: string | null
  mime_type: string | null
  file_size_bytes: number | null
  is_public: boolean
  raw_text: string | null
  process_error: string | null
  evidence_note_count: number
  display: { source_type_label: string; status_label: string } | null
  created_at: string | null
}

interface Philosophy {
  id: string
  version: number
  status: string
  positioning_statement: string | null
}

const SOURCE_TYPE_OPTIONS: Array<{ value: string; label: string; group: 'TEXT' | 'PHOTO' }> = [
  { value: 'HOMEPAGE', label: '병원 홈페이지', group: 'TEXT' },
  { value: 'NAVER_BLOG', label: '네이버 블로그', group: 'TEXT' },
  { value: 'YOUTUBE', label: '유튜브', group: 'TEXT' },
  { value: 'INTERVIEW', label: '원장 인터뷰지', group: 'TEXT' },
  { value: 'BROCHURE', label: '브로슈어', group: 'TEXT' },
  { value: 'LANDING_PAGE', label: '랜딩 페이지', group: 'TEXT' },
  { value: 'INTERNAL_NOTE', label: '내부 메모', group: 'TEXT' },
  { value: 'OTHER', label: '기타', group: 'TEXT' },
  { value: 'PHOTO_DOCTOR', label: '사진 — 원장', group: 'PHOTO' },
  { value: 'PHOTO_CLINIC_EXTERIOR', label: '사진 — 외관', group: 'PHOTO' },
  { value: 'PHOTO_CLINIC_INTERIOR', label: '사진 — 내부', group: 'PHOTO' },
  { value: 'PHOTO_TREATMENT_ROOM', label: '사진 — 진료/시술실', group: 'PHOTO' },
]

type StepKey = 'profile' | 'sources' | 'processing' | 'philosophy_draft' | 'philosophy_approved'

interface StepDef {
  key: StepKey
  index: number
  title: string
  description: string
  href?: string
  status: 'completed' | 'current' | 'upcoming'
}

function deriveSteps(
  hospital: Hospital | null,
  sources: Source[],
  philosophies: Philosophy[],
  hospitalId: string,
): StepDef[] {
  const profileDone = !!hospital?.profile_complete
  const hasSource = sources.length > 0
  const anyProcessed = sources.some((s) => s.status === 'PROCESSED')
  const draftReady = philosophies.some((p) => p.status === 'DRAFT' || p.status === 'APPROVED')
  const approved = philosophies.some((p) => p.status === 'APPROVED')

  const completed = [profileDone, hasSource, anyProcessed, draftReady, approved]
  const firstUpcomingIdx = completed.findIndex((c) => !c)

  const status = (idx: number): StepDef['status'] => {
    if (firstUpcomingIdx === -1) return 'completed'
    if (idx < firstUpcomingIdx) return 'completed'
    if (idx === firstUpcomingIdx) return 'current'
    return 'upcoming'
  }

  return [
    {
      key: 'profile',
      index: 0,
      title: '병원 프로파일 입력',
      description: '병원명·진료과·원장명·진료시간·주소 등 기본 정보. 완료 시 V0 리포트 자동 트리거.',
      href: `/hospitals/${hospitalId}/profile`,
      status: status(0),
    },
    {
      key: 'sources',
      index: 1,
      title: '병원 자산 인입',
      description: '홈페이지 URL, 인터뷰 PDF/DOCX, 사진을 업로드합니다.',
      status: status(1),
    },
    {
      key: 'processing',
      index: 2,
      title: '자료 처리',
      description: '각 자료에서 근거 노트(claim + 출처 발췌)를 추출합니다.',
      status: status(2),
    },
    {
      key: 'philosophy_draft',
      index: 3,
      title: '운영 기준 초안 검토',
      description: '추출된 근거 노트로 콘텐츠 운영 기준 초안을 생성·검토합니다.',
      href: `/hospitals/${hospitalId}/essence`,
      status: status(3),
    },
    {
      key: 'philosophy_approved',
      index: 4,
      title: '운영 기준 승인 → 콘텐츠 시작',
      description: '근거 검토 확인 후 승인 → 콘텐츠 자동 생성 사이클이 시작됩니다.',
      href: `/hospitals/${hospitalId}/essence`,
      status: status(4),
    },
  ]
}

export default function OnboardingPage() {
  const { id } = useParams<{ id: string }>()
  const [hospital, setHospital] = useState<Hospital | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [philosophies, setPhilosophies] = useState<Philosophy[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [h, s, p] = await Promise.all([
        fetchAPI(`/admin/hospitals/${id}`),
        fetchAPI(`/admin/hospitals/${id}/essence/sources`),
        fetchAPI(`/admin/hospitals/${id}/essence/philosophies`),
      ])
      setHospital(h as Hospital)
      setSources(Array.isArray(s) ? (s as Source[]) : [])
      setPhilosophies(Array.isArray(p) ? (p as Philosophy[]) : [])
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '로딩 실패')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const steps = useMemo(
    () => deriveSteps(hospital, sources, philosophies, id),
    [hospital, sources, philosophies, id],
  )
  const completedCount = steps.filter((s) => s.status === 'completed').length

  return (
    <main className="p-8 space-y-6 bg-slate-50 min-h-full">
      <header className="rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-blue-900 p-7 text-white shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-blue-200">신규 병원 온보딩</p>
        <h1 className="mt-2 text-2xl font-bold">{hospital?.name ?? '온보딩'}</h1>
        <p className="mt-2 text-sm leading-6 text-blue-50/90 max-w-2xl">
          AE가 한 화면에서 프로파일 → 자산 인입 → 처리 → 운영 기준 승인까지 진행합니다. 단계 완료 시
          자동으로 다음 단계가 활성화됩니다.
        </p>
        <div className="mt-4 flex items-center gap-3">
          <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white">
            진행 {completedCount}/{steps.length} 단계
          </span>
          <button
            onClick={refresh}
            className="text-xs font-medium text-blue-100 hover:text-white underline underline-offset-2"
          >
            새로 고침
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      <section className="grid gap-4 lg:grid-cols-[260px_1fr]">
        {/* Sidebar progress */}
        <aside className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm self-start">
          <ol className="space-y-1">
            {steps.map((s) => (
              <StepBadge key={s.key} step={s} />
            ))}
          </ol>
        </aside>

        <div className="space-y-4">
          {steps.map((s) => (
            <StepCard
              key={s.key}
              step={s}
              hospital={hospital}
              sources={sources}
              philosophies={philosophies}
              hospitalId={id}
              loading={loading}
              onChanged={refresh}
            />
          ))}
        </div>
      </section>
    </main>
  )
}

function StepBadge({ step }: { step: StepDef }) {
  const tone =
    step.status === 'completed'
      ? 'bg-green-50 text-green-700 border-green-200'
      : step.status === 'current'
        ? 'bg-blue-50 text-blue-700 border-blue-200'
        : 'bg-slate-50 text-slate-500 border-slate-200'
  const mark = step.status === 'completed' ? '✓' : step.status === 'current' ? '●' : '○'
  return (
    <li>
      <a
        href={`#step-${step.index}`}
        className={`flex items-start gap-3 rounded-lg border px-3 py-2 ${tone} transition`}
      >
        <span className="text-lg leading-none">{mark}</span>
        <span>
          <span className="block text-xs font-semibold uppercase tracking-wider">STEP {step.index + 1}</span>
          <span className="block text-sm font-medium">{step.title}</span>
        </span>
      </a>
    </li>
  )
}

function StepCard({
  step,
  hospital,
  sources,
  philosophies,
  hospitalId,
  loading,
  onChanged,
}: {
  step: StepDef
  hospital: Hospital | null
  sources: Source[]
  philosophies: Philosophy[]
  hospitalId: string
  loading: boolean
  onChanged: () => void
}) {
  const tone =
    step.status === 'completed'
      ? 'border-green-200'
      : step.status === 'current'
        ? 'border-blue-300 ring-1 ring-blue-200'
        : 'border-slate-200'

  return (
    <article id={`step-${step.index}`} className={`rounded-2xl border ${tone} bg-white shadow-sm`}>
      <header className="flex items-start justify-between gap-3 px-6 py-5 border-b border-slate-100">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-blue-600">
            STEP {step.index + 1} / {5}
          </p>
          <h2 className="mt-1 text-lg font-bold text-slate-900">{step.title}</h2>
          <p className="mt-1 text-sm text-slate-600 max-w-2xl">{step.description}</p>
        </div>
        <StepStatusChip status={step.status} />
      </header>

      <div className="px-6 py-5">
        {step.key === 'profile' && (
          <ProfileStepBody hospital={hospital} hospitalId={hospitalId} />
        )}
        {step.key === 'sources' && (
          <SourcesStepBody hospitalId={hospitalId} sources={sources} onChanged={onChanged} loading={loading} />
        )}
        {step.key === 'processing' && (
          <ProcessingStepBody hospitalId={hospitalId} sources={sources} onChanged={onChanged} />
        )}
        {step.key === 'philosophy_draft' && (
          <PhilosophyStepBody
            hospitalId={hospitalId}
            philosophies={philosophies}
            sources={sources}
            mode="draft"
          />
        )}
        {step.key === 'philosophy_approved' && (
          <PhilosophyStepBody
            hospitalId={hospitalId}
            philosophies={philosophies}
            sources={sources}
            mode="approve"
          />
        )}
      </div>
    </article>
  )
}

function StepStatusChip({ status }: { status: StepDef['status'] }) {
  const map = {
    completed: { label: '완료', cls: 'bg-green-100 text-green-700' },
    current: { label: '진행 필요', cls: 'bg-blue-100 text-blue-700' },
    upcoming: { label: '대기', cls: 'bg-slate-100 text-slate-500' },
  } as const
  const { label, cls } = map[status]
  return (
    <span className={`shrink-0 rounded-full px-3 py-1 text-xs font-semibold ${cls}`}>{label}</span>
  )
}

function ProfileStepBody({ hospital, hospitalId }: { hospital: Hospital | null; hospitalId: string }) {
  return (
    <div className="space-y-3">
      <ul className="text-sm text-slate-700 space-y-1">
        <li>· 프로파일 완료: {hospital?.profile_complete ? '✓' : '미완료'}</li>
        <li>· V0 리포트: {hospital?.v0_report_done ? '✓' : '미생성'}</li>
        <li>· 콘텐츠 스케줄: {hospital?.schedule_set ? '✓' : '미설정'}</li>
      </ul>
      <Link
        href={`/hospitals/${hospitalId}/profile`}
        className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
      >
        프로파일 화면으로 →
      </Link>
    </div>
  )
}

function SourcesStepBody({
  hospitalId,
  sources,
  onChanged,
  loading,
}: {
  hospitalId: string
  sources: Source[]
  onChanged: () => void
  loading: boolean
}) {
  return (
    <div className="space-y-5">
      <CrawlForm hospitalId={hospitalId} onCreated={onChanged} />
      <UploadForm hospitalId={hospitalId} onCreated={onChanged} />
      <SourcesList hospitalId={hospitalId} sources={sources} loading={loading} onChanged={onChanged} />
    </div>
  )
}

function CrawlForm({ hospitalId, onCreated }: { hospitalId: string; onCreated: () => void }) {
  const [type, setType] = useState('HOMEPAGE')
  const [title, setTitle] = useState('')
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setFeedback(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/crawl`, {
        method: 'POST',
        body: JSON.stringify({ source_type: type, title, url }),
      })
      setTitle('')
      setUrl('')
      setFeedback('URL 크롤 완료. 본문이 자동 추출됐습니다.')
      onCreated()
    } catch (e: unknown) {
      setFeedback(e instanceof Error ? e.message : '실패')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-3">
      <h3 className="text-sm font-bold text-slate-900">URL로 자료 추가 (자동 크롤)</h3>
      <div className="grid gap-2 md:grid-cols-[160px_1fr]">
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          {SOURCE_TYPE_OPTIONS.filter((o) => o.group === 'TEXT').map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <input
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="자료 제목 (예: 병원 공식 홈페이지)"
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        />
      </div>
      <input
        required
        type="url"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://..."
        className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
      />
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? '크롤 중…' : 'URL 자동 크롤'}
        </button>
        {feedback && <span className="text-xs text-slate-600">{feedback}</span>}
      </div>
    </form>
  )
}

function UploadForm({ hospitalId, onCreated }: { hospitalId: string; onCreated: () => void }) {
  const [type, setType] = useState('PHOTO_DOCTOR')
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setFeedback(null)
    try {
      const fd = new FormData()
      fd.append('source_type', type)
      fd.append('title', title)
      fd.append('file', file)
      const res = await fetch(`/api/admin/hospitals/${hospitalId}/essence/sources/upload`, {
        method: 'POST',
        body: fd,
      })
      if (!res.ok) {
        const errorText = await res.text()
        throw new Error(errorText || `HTTP ${res.status}`)
      }
      setTitle('')
      setFile(null)
      // reset file input
      const inp = document.getElementById('upload-file') as HTMLInputElement | null
      if (inp) inp.value = ''
      setFeedback('업로드 완료.')
      onCreated()
    } catch (e: unknown) {
      setFeedback(e instanceof Error ? e.message : '실패')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-3">
      <h3 className="text-sm font-bold text-slate-900">파일 업로드 (사진 / PDF / DOCX)</h3>
      <div className="grid gap-2 md:grid-cols-[200px_1fr]">
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          <optgroup label="사진">
            {SOURCE_TYPE_OPTIONS.filter((o) => o.group === 'PHOTO').map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </optgroup>
          <optgroup label="문서">
            {SOURCE_TYPE_OPTIONS.filter((o) => o.group === 'TEXT').map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </optgroup>
        </select>
        <input
          required
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="자료 제목"
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        />
      </div>
      <input
        id="upload-file"
        required
        type="file"
        accept="image/*,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        className="block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm"
      />
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy || !file}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? '업로드 중…' : '업로드'}
        </button>
        {feedback && <span className="text-xs text-slate-600">{feedback}</span>}
      </div>
    </form>
  )
}

function SourcesList({
  hospitalId,
  sources,
  loading,
  onChanged,
}: {
  hospitalId: string
  sources: Source[]
  loading: boolean
  onChanged: () => void
}) {
  const [excludingId, setExcludingId] = useState<string | null>(null)
  const [excludeErrors, setExcludeErrors] = useState<Record<string, string>>({})

  async function exclude(sourceId: string) {
    if (!confirm('이 자료를 제외하시겠습니까? 운영 기준 초안과 /site 노출에서 빠집니다.')) return
    setExcludingId(sourceId)
    setExcludeErrors((prev) => {
      const next = { ...prev }
      delete next[sourceId]
      return next
    })
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/${sourceId}/exclude`, {
        method: 'POST',
      })
      onChanged()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '제외 실패'
      setExcludeErrors((prev) => ({ ...prev, [sourceId]: message }))
    } finally {
      setExcludingId(null)
    }
  }

  if (loading && sources.length === 0) {
    return <p className="text-sm text-slate-500">자료 목록을 불러오는 중…</p>
  }
  if (sources.length === 0) {
    return (
      <p className="text-sm text-slate-500 italic">
        아직 등록된 자료가 없습니다. 위 폼으로 첫 자료를 추가해 주세요.
      </p>
    )
  }
  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        등록된 자료 ({sources.length})
      </p>
      <ul className="space-y-2">
        {sources.map((s) => {
          const fileHref = s.file_access_url ?? s.file_url
          return (
            <li
              key={s.id}
              className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-white p-3 text-sm"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 font-medium text-slate-900">
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
                      {s.display?.source_type_label ?? s.source_type}
                    </span>
                    <span className="truncate">{s.title}</span>
                  </p>
                  <p className="mt-1 text-xs text-slate-500 truncate">
                    {s.url ? (
                      <a href={s.url} target="_blank" rel="noopener" className="underline">{s.url}</a>
                    ) : fileHref ? (
                      <a
                        href={fileHref}
                        target="_blank"
                        rel="noopener"
                        className="underline"
                      >
                        파일 보기 ({s.mime_type ?? 'binary'})
                      </a>
                    ) : (
                      '본문 직접 입력'
                    )}
                    {s.evidence_note_count > 0 && ` · 근거 노트 ${s.evidence_note_count}개`}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span
                    className={`rounded-full px-2 py-1 text-xs font-semibold ${
                      s.status === 'PROCESSED'
                        ? 'bg-green-100 text-green-700'
                        : s.status === 'ERROR'
                          ? 'bg-red-100 text-red-700'
                          : s.status === 'EXCLUDED'
                            ? 'bg-slate-100 text-slate-500'
                            : 'bg-yellow-100 text-yellow-800'
                    }`}
                  >
                    {s.display?.status_label ?? s.status}
                  </span>
                  {s.status !== 'EXCLUDED' && (
                    <button
                      onClick={() => exclude(s.id)}
                      disabled={excludingId === s.id}
                      className="rounded border border-slate-300 bg-white px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                    >
                      {excludingId === s.id ? '제외 중…' : '제외'}
                    </button>
                  )}
                </div>
              </div>
              {excludeErrors[s.id] && (
                <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-700">{excludeErrors[s.id]}</p>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}

function ProcessingStepBody({
  hospitalId,
  sources,
  onChanged,
}: {
  hospitalId: string
  sources: Source[]
  onChanged: () => void
}) {
  const pending = sources.filter((s) => s.status === 'PENDING' && (s.raw_text?.trim() ?? '').length > 0)
  const processed = sources.filter((s) => s.status === 'PROCESSED')
  const errored = sources.filter((s) => s.status === 'ERROR')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  async function process(sourceId: string) {
    setBusyId(sourceId)
    setErrors((prev) => {
      const next = { ...prev }
      delete next[sourceId]
      return next
    })
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/${sourceId}/process`, {
        method: 'POST',
      })
      onChanged()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '처리 실패'
      setErrors((prev) => ({ ...prev, [sourceId]: message }))
    } finally {
      setBusyId(null)
    }
  }

  if (sources.length === 0) {
    return <p className="text-sm text-slate-500">먼저 자료를 인입해 주세요.</p>
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-700">
        처리 가능: <strong>{pending.length}</strong>개 · 완료: <strong>{processed.length}</strong>개 ·
        오류: <strong>{errored.length}</strong>개
      </p>
      {pending.length > 0 && (
        <ul className="space-y-2">
          {pending.map((s) => (
            <li
              key={s.id}
              className="flex flex-col gap-2 rounded-lg border border-slate-200 bg-white p-3 text-sm"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="truncate">{s.title}</span>
                <button
                  onClick={() => process(s.id)}
                  disabled={busyId === s.id}
                  className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {busyId === s.id ? '처리 중…' : '처리'}
                </button>
              </div>
              {errors[s.id] && (
                <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-700">{errors[s.id]}</p>
              )}
            </li>
          ))}
        </ul>
      )}
      {processed.length > 0 && (
        <p className="text-xs text-slate-500">
          이미 처리된 자료는 운영 기준 초안에 자동으로 반영됩니다.
        </p>
      )}
      {errored.length > 0 && (
        <div className="space-y-2 rounded-lg border border-red-200 bg-red-50 p-3">
          <p className="text-xs font-semibold text-red-800">처리 중 오류가 발생한 자료 — 재시도 가능</p>
          <ul className="space-y-2">
            {errored.map((s) => (
              <li key={s.id} className="flex flex-col gap-1 rounded bg-white p-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-slate-900">{s.title}</span>
                  <button
                    onClick={() => process(s.id)}
                    disabled={busyId === s.id}
                    className="rounded bg-red-600 px-2 py-1 text-[11px] font-semibold text-white hover:bg-red-700 disabled:opacity-50"
                  >
                    {busyId === s.id ? '재시도 중…' : '다시 처리'}
                  </button>
                </div>
                {(errors[s.id] || s.process_error) && (
                  <p className="text-red-700">{errors[s.id] || s.process_error}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function PhilosophyStepBody({
  hospitalId,
  philosophies,
  sources,
  mode,
}: {
  hospitalId: string
  philosophies: Philosophy[]
  sources: Source[]
  mode: 'draft' | 'approve'
}) {
  const draft = philosophies.find((p) => p.status === 'DRAFT')
  const approved = philosophies.find((p) => p.status === 'APPROVED')

  if (mode === 'draft') {
    if (approved) {
      return (
        <p className="text-sm text-slate-700">
          이미 승인된 운영 기준이 있습니다 (v{approved.version}).{' '}
          <Link href={`/hospitals/${hospitalId}/essence`} className="text-blue-600 underline">
            essence 화면에서 보기 →
          </Link>
        </p>
      )
    }
    if (draft) {
      return (
        <div className="space-y-3 text-sm">
          <p className="text-slate-700">
            <strong>v{draft.version}</strong> 초안이 준비됐습니다.
          </p>
          {draft.positioning_statement && (
            <p className="rounded-lg bg-blue-50 px-3 py-2 text-blue-800 italic">
              {draft.positioning_statement}
            </p>
          )}
          <Link
            href={`/hospitals/${hospitalId}/essence`}
            className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
          >
            essence 화면에서 검토 →
          </Link>
        </div>
      )
    }
    const processedCount = sources.filter((s) => s.status === 'PROCESSED').length
    if (processedCount === 0) {
      return <p className="text-sm text-slate-500">먼저 자료를 처리해 주세요.</p>
    }
    return (
      <p className="text-sm text-slate-700">
        처리된 자료 {processedCount}개로 운영 기준 초안을 생성할 수 있습니다.{' '}
        <Link href={`/hospitals/${hospitalId}/essence`} className="text-blue-600 underline">
          essence 화면에서 초안 생성 →
        </Link>
      </p>
    )
  }

  // mode === 'approve'
  if (approved) {
    return (
      <div className="space-y-2 text-sm">
        <p className="text-green-700 font-semibold">
          ✓ 운영 기준 v{approved.version} 승인 완료
        </p>
        <Link
          href={`/hospitals/${hospitalId}/schedule`}
          className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
        >
          콘텐츠 스케줄 설정으로 →
        </Link>
      </div>
    )
  }
  if (draft) {
    return (
      <p className="text-sm text-slate-700">
        v{draft.version} 초안이 검토 대기 중입니다.{' '}
        <Link href={`/hospitals/${hospitalId}/essence`} className="text-blue-600 underline">
          essence 화면에서 근거 검토 후 승인 →
        </Link>
      </p>
    )
  }
  return <p className="text-sm text-slate-500">초안을 먼저 생성해 주세요.</p>
}
