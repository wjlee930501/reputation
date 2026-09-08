'use client'

import { useCallback, useEffect, useState } from 'react'

import { ADMIN_COPY } from '@/lib/admin-copy'
import { fetchAPI } from '@/lib/api'
import { defaultAssetTitles } from '@/lib/asset-title'
import {
  TEXT_SOURCE_TYPE_OPTIONS,
  groupNotesByType,
  isTextSource,
  processingSummary,
  sourceRowStatus,
  type SourceProcessingRunSummary,
} from '@/lib/info-sections'
import { safeOperatorError } from '@/lib/operations-journey'
import NaverBlogBulkForm from '../onboarding/NaverBlogBulkForm'

interface NoteRow {
  id: string
  note_type: string
  claim: string
  source_excerpt: string
  confidence: number | null
  note_metadata?: Record<string, unknown> | null
}

interface TextSourceRow {
  id: string
  source_type: string
  title: string
  status: string
  url: string | null
  file_url: string | null
  file_access_url: string | null
  evidence_note_count: number
  display: { source_type_label: string } | null
}

interface SourceDetail extends TextSourceRow {
  evidence_notes: NoteRow[] | null
}

const TONE_CLASS: Record<string, string> = {
  neutral: 'bg-slate-100 text-slate-600',
  good: 'bg-green-100 text-green-700',
  paused: 'bg-slate-100 text-slate-500',
  warn: 'bg-amber-100 text-amber-800',
}

function sourceTypeLabel(source: TextSourceRow): string {
  return source.display?.source_type_label
    ?? TEXT_SOURCE_TYPE_OPTIONS.find((option) => option.value === source.source_type)?.label
    ?? '자료 유형 확인 필요'
}

function isNoiseNote(note: NoteRow): boolean {
  return note.note_metadata?.is_noise === true
}

/**
 * 병원 글의 근거가 되는 문서 자료.
 *
 * 공식 채널은 병원 정보 저장이 자동으로 등록하고, 등록된 자료의 처리도 서버가 한다.
 * 사람이 하는 일은 파일을 올리는 것과, 쓰지 않을 자료·노트를 빼는 것뿐이다.
 */
export function SourcesSection({ hospitalId }: { hospitalId: string }) {
  const [sources, setSources] = useState<TextSourceRow[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [run, setRun] = useState<SourceProcessingRunSummary | null>(null)

  const refresh = useCallback(async () => {
    try {
      const all = await fetchAPI<TextSourceRow[]>(`/admin/hospitals/${hospitalId}/essence/sources`)
      setSources((Array.isArray(all) ? all : []).filter(isTextSource))
      setLoadError(null)
    } catch {
      setLoadError(safeOperatorError('onboarding', '근거 자료 목록을 다시 불러오세요.'))
    } finally {
      setLoading(false)
    }
  }, [hospitalId])

  const readRun = useCallback(async () => {
    try {
      setRun(
        await fetchAPI<SourceProcessingRunSummary | null>(
          `/admin/hospitals/${hospitalId}/essence/source-processing-runs/latest`,
        ),
      )
    } catch {
      // 진행 표시는 부가 정보다. 못 읽어도 목록과 업로드는 그대로 쓸 수 있다.
    }
  }, [hospitalId])

  useEffect(() => {
    void refresh()
    void readRun()
  }, [refresh, readRun])

  const summary = processingSummary(run)

  // 진행 중일 때만 다시 읽는다 — 끝난 뒤에는 표의 상태가 이미 결과를 말한다.
  useEffect(() => {
    if (!summary) return
    const timer = window.setInterval(() => {
      void readRun()
      void refresh()
    }, 15000)
    return () => window.clearInterval(timer)
  }, [summary, readRun, refresh])

  return (
    <section
      id="info-sources"
      className="scroll-mt-24 space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-base font-semibold text-slate-800">{ADMIN_COPY.evidence}</h3>
          <p className="mt-0.5 text-xs leading-5 text-slate-500">
            공식 채널은 병원 정보를 저장할 때 자동으로 등록되고 처리됩니다. 문서 파일만 직접 올리면 됩니다.
          </p>
        </div>
        {summary && (
          <span
            role="status"
            className="rounded-full bg-blue-100 px-3 py-1 text-xs font-semibold text-blue-800"
          >
            {summary}
          </span>
        )}
      </div>

      <SourceUploadForm hospitalId={hospitalId} onUploaded={() => { void refresh(); void readRun() }} />

      <NaverBlogBulkForm hospitalId={hospitalId} onCreated={() => void refresh()} />

      {loadError && <p className="text-sm font-semibold text-red-700">{loadError}</p>}
      {loading ? (
        <p className="text-sm text-slate-500">{ADMIN_COPY.evidence} 목록을 불러오는 중…</p>
      ) : sources.length === 0 ? (
        <p className="text-sm italic text-slate-500">
          아직 등록된 {ADMIN_COPY.evidence}가 없습니다. 공식 채널을 저장하거나 문서를 올리면 여기에 표시됩니다.
        </p>
      ) : (
        <ul className="space-y-2">
          {sources.map((source) => (
            <SourceRow
              key={source.id}
              hospitalId={hospitalId}
              source={source}
              onChanged={() => void refresh()}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

function SourceRow({
  hospitalId,
  source,
  onChanged,
}: {
  hospitalId: string
  source: TextSourceRow
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notes, setNotes] = useState<NoteRow[] | null>(null)
  const [notesLoading, setNotesLoading] = useState(false)
  const status = sourceRowStatus(source)
  const href = source.url ?? source.file_access_url ?? source.file_url
  const excluded = source.status === 'EXCLUDED'

  // 노트는 펼칠 때 한 번만 받는다 — 자료 수만큼 상세를 미리 부르지 않는다.
  async function loadNotes() {
    if (notes !== null || notesLoading) return
    setNotesLoading(true)
    try {
      const detail = await fetchAPI<SourceDetail>(
        `/admin/hospitals/${hospitalId}/essence/sources/${source.id}`,
      )
      setNotes(detail.evidence_notes ?? [])
      setError(null)
    } catch {
      setError(safeOperatorError('onboarding', `${ADMIN_COPY.evidenceNote}를 다시 펼쳐 보세요.`))
    } finally {
      setNotesLoading(false)
    }
  }

  async function setExcluded(next: boolean) {
    if (next && !confirm('이 자료를 제외하시겠습니까? 새 글의 근거와 병원 공개 페이지에서 빠집니다.')) {
      return
    }
    setBusy(true)
    setError(null)
    try {
      const path = next
        ? `/admin/hospitals/${hospitalId}/essence/sources/${source.id}/exclude`
        : `/admin/hospitals/${hospitalId}/essence/sources/${source.id}/reinclude`
      await fetchAPI(path, { method: 'POST' })
      onChanged()
    } catch {
      setError(
        safeOperatorError(
          'onboarding',
          `${ADMIN_COPY.evidence} 목록을 다시 불러온 뒤 ${next ? '제외' : '제외 해제'}를 다시 누르세요.`,
        ),
      )
    } finally {
      setBusy(false)
    }
  }

  // 표시를 먼저 바꾸고 저장한다. 실패하면 되돌려 화면과 서버가 어긋난 채로 남지 않게 한다.
  async function toggleNoise(noteId: string, isNoise: boolean) {
    const apply = (value: boolean) =>
      setNotes((prev) =>
        prev?.map((note) =>
          note.id === noteId
            ? { ...note, note_metadata: { ...(note.note_metadata ?? {}), is_noise: value } }
            : note,
        ) ?? prev,
      )
    apply(isNoise)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/evidence-notes/noise`, {
        method: 'PATCH',
        body: JSON.stringify({ note_ids: [noteId], is_noise: isNoise }),
      })
    } catch {
      apply(!isNoise)
      setError(safeOperatorError('onboarding', `${ADMIN_COPY.evidenceNote} 제외 표시를 다시 저장하세요.`))
    }
  }

  return (
    <li className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2">
            <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
              {sourceTypeLabel(source)}
            </span>
            <span className="min-w-0 truncate font-medium text-slate-900" title={source.title}>
              {href ? (
                <a href={href} target="_blank" rel="noopener" className="underline">
                  {source.title}
                </a>
              ) : (
                source.title
              )}
            </span>
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {ADMIN_COPY.evidenceNote} {source.evidence_note_count}개
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <span className={`rounded-full px-2 py-1 text-xs font-semibold ${TONE_CLASS[status.tone]}`}>
            {status.label}
          </span>
          <button
            type="button"
            onClick={() => void setExcluded(!excluded)}
            disabled={busy}
            className="min-h-11 rounded border border-slate-300 bg-white px-3 py-2 text-xs font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            {excluded ? '제외 해제' : '제외'}
          </button>
        </div>
      </div>

      {source.evidence_note_count > 0 && (
        <details className="mt-2" onToggle={(e) => { if (e.currentTarget.open) void loadNotes() }}>
          <summary className="cursor-pointer text-xs font-semibold text-blue-700">
            {ADMIN_COPY.evidenceNote} 보기
          </summary>
          {notesLoading && <p className="mt-2 text-xs text-slate-500">불러오는 중…</p>}
          {notes && notes.length === 0 && (
            <p className="mt-2 text-xs text-slate-500">표시할 {ADMIN_COPY.evidenceNote}가 없습니다.</p>
          )}
          {notes && notes.length > 0 && (
            <div className="mt-2 space-y-3">
              {groupNotesByType(notes).map((group) => (
                <div key={group.noteType}>
                  <p className="text-xs font-semibold text-slate-700">
                    {group.label} ({group.notes.length}건)
                  </p>
                  <ul className="mt-1 space-y-2">
                    {group.notes.map((note) => (
                      <li
                        key={note.id}
                        className={`rounded-md border p-2 ${
                          isNoiseNote(note) ? 'border-slate-200 bg-slate-50' : 'border-slate-200'
                        }`}
                      >
                        <p className="text-xs font-medium text-slate-900">{note.claim}</p>
                        <blockquote className="mt-1 border-l-2 border-slate-300 bg-slate-50 px-2 py-1 text-[11px] italic text-slate-600">
                          {note.source_excerpt}
                        </blockquote>
                        <label className="mt-1 flex min-h-11 cursor-pointer items-center gap-2 text-[11px] text-slate-600">
                          <input
                            type="checkbox"
                            checked={isNoiseNote(note)}
                            onChange={(e) => void toggleNoise(note.id, e.target.checked)}
                            className="rounded border-slate-300"
                          />
                          제외 자료로 표시
                          {note.confidence !== null && (
                            <span className="text-slate-400">
                              · 신뢰도 {(note.confidence * 100).toFixed(0)}%
                            </span>
                          )}
                        </label>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </details>
      )}

      {error && <p className="mt-2 rounded bg-red-50 px-2 py-1 text-xs text-red-700">{error}</p>}
    </li>
  )
}

/** 문서 파일 업로드. 유형과 파일별 제목만 받고, 처리는 저장 뒤 서버가 이어서 한다. */
function SourceUploadForm({
  hospitalId,
  onUploaded,
}: {
  hospitalId: string
  onUploaded: () => void
}) {
  const [sourceType, setSourceType] = useState(TEXT_SOURCE_TYPE_OPTIONS[0].value)
  const [files, setFiles] = useState<File[]>([])
  const [titles, setTitles] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)

  function selectFiles(picked: FileList | null) {
    const list = picked ? Array.from(picked) : []
    setFiles(list)
    setTitles(defaultAssetTitles(list.map((file) => file.name)))
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (files.length === 0) return
    setBusy(true)
    setFeedback(null)

    let succeeded = 0
    for (let i = 0; i < files.length; i++) {
      const body = new FormData()
      body.append('source_type', sourceType)
      body.append('title', (titles[i] ?? '').trim())
      body.append('file', files[i])
      try {
        await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/upload`, {
          method: 'POST',
          body,
        })
        succeeded += 1
      } catch {
        // 파일별 결과는 아래 요약 한 줄로 알린다.
      }
    }

    const failed = files.length - succeeded
    setFeedback(
      failed === 0
        ? `${succeeded}개 저장 완료. 처리는 자동으로 이어집니다.`
        : succeeded === 0
          ? safeOperatorError('onboarding', `${failed}개 저장 실패. 파일 형식(PDF·DOCX)을 확인한 뒤 다시 저장하세요.`)
          : `${succeeded}개 저장, ${failed}개 실패. 실패한 파일만 다시 선택해 저장하세요.`,
    )
    if (succeeded > 0) {
      selectFiles(null)
      const input = document.getElementById('info-source-file') as HTMLInputElement | null
      if (input) input.value = ''
      onUploaded()
    }
    setBusy(false)
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div className="grid gap-2 md:grid-cols-[200px_1fr]">
        <select
          value={sourceType}
          onChange={(e) => setSourceType(e.target.value)}
          aria-label="자료 유형"
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          {TEXT_SOURCE_TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
        <p className="self-center text-xs leading-5 text-slate-600">
          PDF·DOCX 문서를 올릴 수 있습니다. 여러 개를 한 번에 고를 수 있습니다.
        </p>
      </div>

      <input
        id="info-source-file"
        required
        type="file"
        multiple
        accept="application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        onChange={(e) => selectFiles(e.target.files)}
        className="block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm"
      />

      {files.length > 0 && (
        <ul className="space-y-2">
          {files.map((file, index) => (
            <li
              key={`${file.name}-${index}`}
              className="grid gap-1 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] md:items-center"
            >
              <label
                htmlFor={`info-source-title-${index}`}
                className="truncate text-xs text-slate-500"
                title={file.name}
              >
                {file.name}
              </label>
              <input
                id={`info-source-title-${index}`}
                value={titles[index] ?? ''}
                onChange={(e) =>
                  setTitles((prev) => prev.map((value, i) => (i === index ? e.target.value : value)))
                }
                placeholder="제목 (비워 두면 파일명 사용)"
                className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
              />
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy || files.length === 0}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? '저장 중…' : files.length > 1 ? `${files.length}개 저장` : '문서 저장'}
        </button>
        {feedback && <span className="text-xs text-slate-600">{feedback}</span>}
      </div>
    </form>
  )
}
