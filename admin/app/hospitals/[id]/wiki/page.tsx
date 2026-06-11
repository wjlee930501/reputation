'use client'

import Image from 'next/image'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { fetchAPI } from '@/lib/api'

interface Source {
  id: string
  source_type: string
  title: string
  status: string
  url: string | null
  file_url: string | null
  file_access_url: string | null
  mime_type: string | null
  is_public: boolean
  raw_text: string | null
  evidence_note_count: number
  display: { source_type_label: string; status_label: string } | null
}

interface EvidenceNote {
  id: string
  source_asset_id: string
  note_type: string
  claim: string
  source_excerpt: string
  confidence: number | null
}

interface SourceDetail extends Source {
  evidence_notes: EvidenceNote[] | null
}

const NOTE_TYPE_LABELS: Record<string, string> = {
  KEY_MESSAGE: '핵심 메시지',
  TONE_SIGNAL: '말투 시그널',
  TREATMENT_SIGNAL: '치료 시그널',
  RISK_SIGNAL: '리스크 시그널',
  PATIENT_PROMISE: '환자 약속',
  DOCTOR_PHILOSOPHY: '의료진 철학',
  LOCAL_CONTEXT: '지역 맥락',
  PROOF_POINT: '근거 자료',
  CONFLICT: '상충 메모',
}

const NOTE_GROUP_ORDER = [
  'DOCTOR_PHILOSOPHY',
  'PATIENT_PROMISE',
  'KEY_MESSAGE',
  'TREATMENT_SIGNAL',
  'TONE_SIGNAL',
  'PROOF_POINT',
  'LOCAL_CONTEXT',
  'RISK_SIGNAL',
  'CONFLICT',
]

const PHOTO_TYPES = new Set([
  'PHOTO_DOCTOR',
  'PHOTO_CLINIC_EXTERIOR',
  'PHOTO_CLINIC_INTERIOR',
  'PHOTO_TREATMENT_ROOM',
])

// Client-bundle code: only NEXT_PUBLIC_* env vars are inlined at build time.
// When unset, keep the path relative rather than shipping a localhost fallback.
const ASSETS_BACKEND_BASE = process.env.NEXT_PUBLIC_BACKEND_URL || ''

function resolveAssetUrl(url: string | null): string | null {
  if (!url) return null
  if (url.startsWith('http://') || url.startsWith('https://')) return url
  // 백엔드 file_access_url은 Admin 세션 프록시 경로(/api/admin/...)를 돌려준다.
  // 이 경로는 same-origin으로 그대로 써야 하며, 백엔드 도메인을 붙이면
  // 존재하지 않는 라우트 + CSP 차단으로 미리보기가 깨진다.
  if (url.startsWith('/api/admin/')) return url
  // 순수 백엔드 상대 경로(/assets/... 등)만 백엔드 베이스를 붙인다.
  if (url.startsWith('/')) return `${ASSETS_BACKEND_BASE}${url}`
  return url
}

export default function WikiPage() {
  const { id } = useParams<{ id: string }>()
  const [sources, setSources] = useState<Source[]>([])
  const [details, setDetails] = useState<Record<string, SourceDetail>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const list = (await fetchAPI(`/admin/hospitals/${id}/essence/sources`)) as Source[]
      setSources(Array.isArray(list) ? list : [])
      // 처리 완료 자료의 근거 노트 상세 조회
      const processed = (Array.isArray(list) ? list : []).filter((s) => s.evidence_note_count > 0)
      const detailEntries = await Promise.all(
        processed.map((s) =>
          fetchAPI(`/admin/hospitals/${id}/essence/sources/${s.id}`)
            .then((d) => [s.id, d as SourceDetail] as const)
            .catch(() => null),
        ),
      )
      const next: Record<string, SourceDetail> = {}
      for (const entry of detailEntries) {
        if (entry) next[entry[0]] = entry[1]
      }
      setDetails(next)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : '로딩 실패')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const allNotes = useMemo(() => {
    const notes: Array<EvidenceNote & { source_title: string; source_type_label: string }> = []
    for (const s of sources) {
      const d = details[s.id]
      if (!d?.evidence_notes) continue
      for (const note of d.evidence_notes) {
        notes.push({
          ...note,
          source_title: s.title,
          source_type_label: s.display?.source_type_label ?? s.source_type,
        })
      }
    }
    return notes
  }, [sources, details])

  const notesByGroup = useMemo(() => {
    const grouped = new Map<string, typeof allNotes>()
    for (const note of allNotes) {
      const list = grouped.get(note.note_type) ?? []
      list.push(note)
      grouped.set(note.note_type, list)
    }
    return grouped
  }, [allNotes])

  const orderedGroups = useMemo(() => {
    const known = NOTE_GROUP_ORDER.filter((t) => notesByGroup.has(t))
    const extras = [...notesByGroup.keys()].filter((t) => !NOTE_GROUP_ORDER.includes(t))
    return [...known, ...extras]
  }, [notesByGroup])

  const photos = sources.filter((s) => PHOTO_TYPES.has(s.source_type))
  const [toggleErrors, setToggleErrors] = useState<Record<string, string>>({})
  const [pendingToggleId, setPendingToggleId] = useState<string | null>(null)

  async function togglePublic(photoId: string, next: boolean) {
    setPendingToggleId(photoId)
    setToggleErrors((prev) => {
      const copy = { ...prev }
      delete copy[photoId]
      return copy
    })
    try {
      await fetchAPI(`/admin/hospitals/${id}/essence/sources/${photoId}/public`, {
        method: 'PATCH',
        body: JSON.stringify({ is_public: next }),
      })
      await refresh()
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '토글 실패'
      setToggleErrors((prev) => ({ ...prev, [photoId]: message }))
    } finally {
      setPendingToggleId(null)
    }
  }

  return (
    <main className="p-8 space-y-6 bg-slate-50 min-h-full">
      <header className="rounded-2xl bg-white border border-slate-200 p-6 shadow-sm">
        <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-blue-600">
          Wiki — 검증된 사실 모음
        </p>
        <h1 className="mt-2 text-2xl font-bold text-slate-900">병원 자산 Wiki</h1>
        <p className="mt-2 text-sm text-slate-600 max-w-2xl">
          AE가 인입한 자료에서 추출된 근거 노트(claim + 출처 발췌)를 카테고리별로 모았습니다.
          사진 자산은 토글로 /site 공개 표면에 노출 여부를 결정합니다.
        </p>
        <div className="mt-3 flex items-center gap-3 text-xs text-slate-600">
          <span>근거 노트 {allNotes.length}개 · 자료 {sources.length}개 · 사진 {photos.length}개</span>
          <button onClick={refresh} className="text-blue-600 hover:underline">새로 고침</button>
          <Link href={`/hospitals/${id}/onboarding`} className="text-blue-600 hover:underline">
            온보딩 화면 →
          </Link>
        </div>
      </header>

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* 사진 토글 */}
      <section className="rounded-2xl bg-white border border-slate-200 shadow-sm">
        <div className="flex items-start justify-between gap-3 px-6 py-5 border-b border-slate-100">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-blue-600">
              Photos · /site 노출 게이트
            </p>
            <h2 className="mt-1 text-lg font-bold text-slate-900">사진 자산 ({photos.length})</h2>
            <p className="mt-1 text-sm text-slate-600">
              검수 완료된 사진만 토글로 공개. 의료광고법 우려 카테고리(환자 후기·전후 사진)는
              데이터 모델에서 차단됨.
            </p>
          </div>
        </div>
        <div className="px-6 py-5">
          {photos.length === 0 ? (
            <p className="text-sm text-slate-500 italic">
              아직 등록된 사진이 없습니다. 온보딩 화면에서 업로드하세요.
            </p>
          ) : (
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
              {photos.map((p) => {
                const resolved = resolveAssetUrl(p.file_access_url ?? p.file_url)
                return (
                  <div key={p.id} className="rounded-xl border border-slate-200 bg-white overflow-hidden">
                    <div className="relative aspect-[4/3] bg-slate-100">
                      {resolved ? (
                        <Image src={resolved} alt={p.title} fill className="object-cover" sizes="240px" unoptimized />
                      ) : (
                        <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-400">
                          미리보기 없음
                        </div>
                      )}
                    </div>
                    <div className="p-3 space-y-2">
                      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
                        {p.display?.source_type_label ?? p.source_type}
                      </p>
                      <p className="text-sm font-medium text-slate-900 truncate">{p.title}</p>
                      <label className="flex items-center gap-2 text-xs text-slate-700 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={p.is_public}
                          disabled={pendingToggleId === p.id}
                          onChange={(e) => togglePublic(p.id, e.target.checked)}
                          className="rounded border-slate-300"
                        />
                        <span>
                          {pendingToggleId === p.id
                            ? '저장 중…'
                            : p.is_public
                              ? '/site에 공개'
                              : '비공개'}
                        </span>
                      </label>
                      {toggleErrors[p.id] && (
                        <p className="rounded bg-red-50 px-2 py-1 text-[11px] text-red-700">
                          {toggleErrors[p.id]}
                        </p>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </section>

      {/* 근거 노트 카테고리별 */}
      {loading && allNotes.length === 0 ? (
        <p className="text-sm text-slate-500 px-2">근거 노트를 불러오는 중…</p>
      ) : allNotes.length === 0 ? (
        <section className="rounded-2xl bg-white border border-slate-200 p-10 text-center">
          <p className="text-base font-semibold text-slate-700">아직 추출된 근거 노트가 없습니다</p>
          <p className="mt-2 text-sm text-slate-500">
            온보딩 화면에서 자료를 인입하고 처리하면 이 자리에 카테고리별로 정리됩니다.
          </p>
          <Link
            href={`/hospitals/${id}/onboarding`}
            className="inline-flex items-center gap-1 mt-4 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
          >
            온보딩으로 →
          </Link>
        </section>
      ) : (
        <section className="space-y-4">
          {orderedGroups.map((groupType) => {
            const list = notesByGroup.get(groupType) ?? []
            const label = NOTE_TYPE_LABELS[groupType] ?? groupType
            return (
              <article
                key={groupType}
                className="rounded-2xl bg-white border border-slate-200 shadow-sm"
              >
                <header className="flex items-center justify-between gap-3 px-6 py-4 border-b border-slate-100">
                  <h2 className="text-base font-bold text-slate-900">{label}</h2>
                  <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
                    {list.length}건
                  </span>
                </header>
                <ul className="divide-y divide-slate-100">
                  {list.map((note) => (
                    <li key={note.id} className="px-6 py-4 space-y-2 text-sm">
                      <p className="font-semibold text-slate-900">{note.claim}</p>
                      <blockquote className="rounded-lg border-l-2 border-slate-300 bg-slate-50 px-3 py-2 italic text-slate-700">
                        {note.source_excerpt}
                      </blockquote>
                      <p className="text-xs text-slate-500">
                        출처: {note.source_type_label} · {note.source_title}
                        {note.confidence !== null && ` · 신뢰도 ${(note.confidence * 100).toFixed(0)}%`}
                      </p>
                    </li>
                  ))}
                </ul>
              </article>
            )
          })}
        </section>
      )}
    </main>
  )
}
