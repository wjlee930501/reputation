'use client'

import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { fetchAPI } from '@/lib/api'
import {
  deriveOnboardingSteps,
  deriveOnboardingSummary,
  type LifecycleReadiness,
  type OnboardingStep as StepDef,
} from '@/lib/onboarding-lifecycle'

interface Hospital {
  id: string
  name: string
  slug: string
  profile_complete: boolean
  v0_report_done: boolean
  schedule_set: boolean
  site_built?: boolean
  site_live?: boolean
  status: string
  aeo_domain?: string | null
  website_url?: string | null
  blog_url?: string | null
  kakao_channel_url?: string | null
  google_business_profile_url?: string | null
  google_maps_url?: string | null
  naver_place_url?: string | null
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

interface UrlCandidate {
  key: string
  title: string
  sourceType: string
  url: string
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

function hasProcessableText(source: Source): boolean {
  return (source.raw_text?.trim() ?? '').length > 0
}

function getProcessingBlockReason(source: Source): string | null {
  if (source.status === 'EXCLUDED' || source.status === 'PROCESSED') return null
  if (hasProcessableText(source)) return null

  if (source.source_type.startsWith('PHOTO_')) {
    return '사진 자료는 공개 자산으로만 사용되며 본문 근거 추출 대상이 아닙니다.'
  }
  if (source.url) {
    return '이 자료는 본문이 없어 근거 추출할 수 없습니다. 자동 크롤을 다시 시도하거나 본문이 있는 문서/메모를 추가해 주세요.'
  }
  if (source.file_url || source.file_access_url) {
    return '업로드 파일에서 추출된 본문이 없습니다. 텍스트가 포함된 PDF/DOCX인지 확인하거나 인터뷰 메모를 추가해 주세요.'
  }
  return '본문이 없어 근거 추출할 수 없습니다. 본문 직접 입력 자료를 추가해 주세요.'
}

function normalizeUrl(value: string | null | undefined): string | null {
  const cleaned = value?.trim()
  if (!cleaned) return null
  return cleaned.replace(/\/+$/, '').toLowerCase()
}

function getProfileUrlCandidates(hospital: Hospital | null, sources: Source[]): UrlCandidate[] {
  if (!hospital) return []
  const existingUrls = new Set(
    sources
      .map((source) => normalizeUrl(source.url))
      .filter((url): url is string => Boolean(url)),
  )
  const candidates: UrlCandidate[] = [
    {
      key: 'website_url',
      title: '병원 공식 홈페이지',
      sourceType: 'HOMEPAGE',
      url: hospital.website_url ?? '',
    },
    {
      key: 'blog_url',
      title: '공식 블로그',
      sourceType: 'NAVER_BLOG',
      url: hospital.blog_url ?? '',
    },
    {
      key: 'naver_place_url',
      title: '네이버 플레이스',
      sourceType: 'OTHER',
      url: hospital.naver_place_url ?? '',
    },
    {
      key: 'google_business_profile_url',
      title: '구글 비즈니스 프로필',
      sourceType: 'OTHER',
      url: hospital.google_business_profile_url ?? '',
    },
    {
      key: 'google_maps_url',
      title: '구글 지도',
      sourceType: 'OTHER',
      url: hospital.google_maps_url ?? '',
    },
    {
      key: 'kakao_channel_url',
      title: '카카오 채널',
      sourceType: 'OTHER',
      url: hospital.kakao_channel_url ?? '',
    },
  ]

  return candidates.filter((candidate) => {
    const normalized = normalizeUrl(candidate.url)
    return normalized !== null && !existingUrls.has(normalized)
  })
}

export default function OnboardingPage() {
  const { id } = useParams<{ id: string }>()
  const [hospital, setHospital] = useState<Hospital | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [philosophies, setPhilosophies] = useState<Philosophy[]>([])
  const [readiness, setReadiness] = useState<LifecycleReadiness | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [h, s, p, r] = await Promise.all([
        fetchAPI(`/admin/hospitals/${id}`),
        fetchAPI(`/admin/hospitals/${id}/essence/sources`),
        fetchAPI(`/admin/hospitals/${id}/essence/philosophies`),
        fetchAPI<LifecycleReadiness>(`/admin/hospitals/${id}/readiness`),
      ])
      setHospital(h as Hospital)
      setSources(Array.isArray(s) ? (s as Source[]) : [])
      setPhilosophies(Array.isArray(p) ? (p as Philosophy[]) : [])
      setReadiness(r)
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
    () => deriveOnboardingSteps(hospital, sources, philosophies, readiness, id),
    [hospital, sources, philosophies, readiness, id],
  )
  const summary = useMemo(
    () => deriveOnboardingSummary(steps, readiness),
    [steps, readiness],
  )
  const completedCount = steps.filter((s) => s.status === 'completed').length

  return (
    <main className="p-8 space-y-6 bg-slate-50 min-h-full">
      <header className="rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-blue-900 p-7 text-white shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-blue-200">신규 병원 온보딩</p>
        <h1 className="mt-2 text-2xl font-bold">{hospital?.name ?? '온보딩'}</h1>
        <p className="mt-2 text-sm leading-6 text-blue-50/90 max-w-2xl">
          프로파일부터 LIVE, 근거 자료, 운영 기준, 스케줄, 첫 발행, AI 답변 언급률 측정까지 실제 상태로 검증합니다.
        </p>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className="rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white">
            진행 {completedCount}/{steps.length} 단계
          </span>
          <span className={`rounded-full px-3 py-1 text-xs font-semibold ${summary.stateClassName}`}>
            {summary.stateLabel}
          </span>
          <button
            onClick={refresh}
            className="text-xs font-medium text-blue-100 hover:text-white underline underline-offset-2"
          >
            새로 고침
          </button>
        </div>
        <div className="mt-5 rounded-xl border border-white/15 bg-white/10 p-4">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-blue-100">현재 상태</p>
              <p className="mt-1 text-lg font-bold text-white">{summary.headline}</p>
              <p className="mt-1 max-w-3xl text-sm leading-6 text-blue-50/90">{summary.detail}</p>
              {summary.blockedReason && (
                <p className="mt-2 text-sm font-semibold text-red-100">차단 사유: {summary.blockedReason}</p>
              )}
            </div>
            <a
              href={summary.nextActionHref}
              className="inline-flex shrink-0 items-center justify-center rounded-lg bg-white px-4 py-2 text-sm font-semibold text-slate-900 hover:bg-blue-50"
            >
              다음 작업: {summary.nextActionLabel}
            </a>
          </div>
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
            STEP {step.index + 1} / {11}
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
          <SourcesStepBody
            hospital={hospital}
            hospitalId={hospitalId}
            sources={sources}
            onChanged={onChanged}
            loading={loading}
          />
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
        {(['v0', 'site', 'live', 'schedule', 'first_publish', 'sov'] as const).includes(
          step.key as 'v0' | 'site' | 'live' | 'schedule' | 'first_publish' | 'sov',
        ) && (
          <OperationalStepBody step={step} />
        )}
      </div>
    </article>
  )
}

function OperationalStepBody({ step }: { step: StepDef }) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-slate-700">
        {step.status === 'completed'
          ? '백엔드 운영 준비도와 실제 저장 데이터에서 완료를 확인했습니다.'
          : '화면 표시용 추정값이 아니라 백엔드 운영 준비도 검사를 통과해야 완료됩니다.'}
      </p>
      {step.href && (
        <Link
          href={step.href}
          className="inline-flex shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:border-blue-200 hover:text-blue-700"
        >
          {step.status === 'completed' ? '상태 확인' : '단계 진행'} →
        </Link>
      )}
    </div>
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
        <li>· 필수 프로파일은 화면 체크리스트와 백엔드 검증을 모두 통과해야 합니다.</li>
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
  hospital,
  hospitalId,
  sources,
  onChanged,
  loading,
}: {
  hospital: Hospital | null
  hospitalId: string
  sources: Source[]
  onChanged: () => void
  loading: boolean
}) {
  return (
    <div className="space-y-5">
      <ProfileUrlCandidates hospital={hospital} hospitalId={hospitalId} sources={sources} onChanged={onChanged} />
      <CrawlForm hospitalId={hospitalId} onCreated={onChanged} />
      <NaverBlogBulkForm hospitalId={hospitalId} onCreated={onChanged} />
      <UploadForm hospitalId={hospitalId} onCreated={onChanged} />
      <SourcesList hospitalId={hospitalId} sources={sources} loading={loading} onChanged={onChanged} />
    </div>
  )
}

function ProfileUrlCandidates({
  hospital,
  hospitalId,
  sources,
  onChanged,
}: {
  hospital: Hospital | null
  hospitalId: string
  sources: Source[]
  onChanged: () => void
}) {
  const [addingKey, setAddingKey] = useState<string | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const candidates = getProfileUrlCandidates(hospital, sources)

  async function addCandidate(candidate: UrlCandidate) {
    setAddingKey(candidate.key)
    setFeedback(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources`, {
        method: 'POST',
        body: JSON.stringify({
          source_type: candidate.sourceType,
          title: candidate.title,
          url: candidate.url,
        }),
      })
      setFeedback(`${candidate.title} 자료를 추가했습니다.`)
      onChanged()
    } catch (e: unknown) {
      setFeedback(e instanceof Error ? e.message : '자료 추가에 실패했습니다.')
    } finally {
      setAddingKey(null)
    }
  }

  if (candidates.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
        프로파일에 새로 추가할 공식 URL 후보가 없습니다.
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-blue-100 bg-blue-50 p-4">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-bold text-blue-950">프로파일 URL 자료 후보</h3>
        <p className="text-xs text-blue-700">
          프로파일에 입력된 공식 채널을 다시 입력하지 않고 자료 인입으로 보낼 수 있습니다.
        </p>
      </div>
      <ul className="mt-3 space-y-2">
        {candidates.map((candidate) => (
          <li
            key={candidate.key}
            className="flex flex-col gap-2 rounded-lg border border-blue-100 bg-white p-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="min-w-0">
              <p className="text-sm font-semibold text-slate-900">{candidate.title}</p>
              <p className="truncate text-xs text-slate-500">{candidate.url}</p>
            </div>
            <button
              type="button"
              onClick={() => addCandidate(candidate)}
              disabled={addingKey === candidate.key}
              className="shrink-0 rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {addingKey === candidate.key ? '추가 중...' : '자료로 추가'}
            </button>
          </li>
        ))}
      </ul>
      {feedback && <p className="mt-2 text-xs text-blue-800">{feedback}</p>}
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

function NaverBlogBulkForm({ hospitalId, onCreated }: { hospitalId: string; onCreated: () => void }) {
  const [url, setUrl] = useState('')
  const [maxPosts, setMaxPosts] = useState(5)
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setFeedback(null)
    try {
      const res = (await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/crawl-blog`, {
        method: 'POST',
        body: JSON.stringify({ url, max_posts: maxPosts }),
      })) as {
        created: number
        skipped_duplicate: number
        skipped_empty: number
        failed: { url: string; reason: string }[]
      }
      setUrl('')
      setFeedback(
        `${res.created}개 글 추가 · 중복 ${res.skipped_duplicate} · 본문없음 ${res.skipped_empty}` +
          (res.failed?.length ? ` · 실패 ${res.failed.length}` : ''),
      )
      onCreated()
    } catch (e: unknown) {
      setFeedback(e instanceof Error ? e.message : '실패')
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-3">
      <h3 className="text-sm font-bold text-slate-900">네이버 블로그 일괄 가져오기 (RSS)</h3>
      <p className="text-xs text-slate-500">
        블로그 주소 또는 blogId를 입력하면 최근 글 본문을 한 번에 수집합니다 (모바일 본문 기준, 중복 자동 제외).
      </p>
      <div className="grid gap-2 md:grid-cols-[1fr_120px]">
        <input
          required
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://blog.naver.com/병원아이디 또는 병원아이디"
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        />
        <select
          value={maxPosts}
          onChange={(e) => setMaxPosts(Number(e.target.value))}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          {[5, 10, 15].map((n) => (
            <option key={n} value={n}>최근 {n}개</option>
          ))}
        </select>
      </div>
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? '가져오는 중…' : '블로그 일괄 가져오기'}
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
      // fetchAPI 사용: 401 시 로그인 리다이렉트, 오류 메시지 한국어 변환 공통 처리
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/upload`, {
        method: 'POST',
        body: fd,
      })
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
  const errored = sources.filter((s) => s.status === 'ERROR' && hasProcessableText(s))
  const blocked = sources.filter((s) => !!getProcessingBlockReason(s))
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
        오류: <strong>{errored.length}</strong>개 · 차단: <strong>{blocked.length}</strong>개
      </p>
      {processed.length > 0 && pending.length === 0 && errored.length === 0 && blocked.length === 0 && (
        <div className="rounded-lg border border-green-200 bg-green-50 p-3 text-sm text-green-800">
          근거 추출이 완료됐습니다. 운영 기준 초안 생성 단계로 진행할 수 있습니다.
        </div>
      )}
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
      {blocked.length > 0 && (
        <div className="space-y-2 rounded-lg border border-yellow-200 bg-yellow-50 p-3">
          <p className="text-xs font-semibold text-yellow-900">근거 추출할 수 없는 자료</p>
          <ul className="space-y-2">
            {blocked.map((s) => (
              <li key={s.id} className="flex flex-col gap-2 rounded bg-white p-2 text-xs">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium text-slate-900">{s.title}</span>
                  <button
                    type="button"
                    disabled
                    title={getProcessingBlockReason(s) ?? undefined}
                    className="rounded bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-400"
                  >
                    근거 추출 불가
                  </button>
                </div>
                <p className="text-yellow-900">{getProcessingBlockReason(s)}</p>
              </li>
            ))}
          </ul>
        </div>
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
