'use client'

import { useCallback, useEffect, useState } from 'react'

import { fetchAPI } from '@/lib/api'
import { defaultAssetTitles } from '@/lib/asset-title'
import {
  PHOTO_SOURCE_TYPE_OPTIONS,
  isPhotoSourceType,
  photoUploadFormData,
} from '@/lib/info-sections'
import { safeOperatorError } from '@/lib/operations-journey'
import { PHOTO_PUBLIC_GATE_COPY, describePhotoPublicGate } from '@/lib/photo-public-gate'
import { formatActorLabel } from '@/lib/actor-display'
import {
  PhotoRightsFields,
  photoRightsReady,
  type PhotoRightsDraft,
} from './PhotoRightsFields'

interface PhotoProvenance {
  source_owner: string | null
  rights_basis: string | null
  rights_basis_label: string | null
  evidence_reference: string | null
  verified_by: string | null
  is_complete: boolean
  missing_message: string | null
}

interface PhotoSource {
  id: string
  source_type: string
  title: string
  status: string
  file_url: string | null
  file_access_url: string | null
  is_public: boolean
  photo_provenance?: PhotoProvenance | null
  display: { source_type_label: string } | null
}

function photoTypeLabel(source: PhotoSource): string {
  return source.display?.source_type_label
    ?? PHOTO_SOURCE_TYPE_OPTIONS.find((option) => option.value === source.source_type)?.label
    ?? '사진'
}

/**
 * 로고 외의 실사진과 그 사용 권리.
 *
 * 사진은 필수가 아니다. 다만 공개되는 사진에는 소유자·권리 근거·증빙 위치가 반드시
 * 있어야 하므로, 업로드와 권리 입력을 한 요청으로 묶고 권리가 빈 사진만 다시 묻는다.
 */
export function PhotosSection({
  hospitalId,
  hospitalName,
}: {
  hospitalId: string
  hospitalName: string | null
}) {
  const [photos, setPhotos] = useState<PhotoSource[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const all = await fetchAPI<PhotoSource[]>(`/admin/hospitals/${hospitalId}/essence/sources`)
      const rows = Array.isArray(all) ? all : []
      setPhotos(
        rows.filter((row) => isPhotoSourceType(row.source_type) && row.status !== 'EXCLUDED'),
      )
      setLoadError(null)
    } catch {
      setLoadError(safeOperatorError('onboarding', '사진 목록을 다시 불러오세요.'))
    } finally {
      setLoading(false)
    }
  }, [hospitalId])

  useEffect(() => {
    void refresh()
  }, [refresh])

  return (
    <section
      id="info-photos"
      className="scroll-mt-24 space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-6"
    >
      <div>
        <h3 className="text-base font-semibold text-slate-800">사진과 사용 권리</h3>
        <p className="mt-0.5 text-xs leading-5 text-slate-500">{PHOTO_PUBLIC_GATE_COPY}</p>
      </div>

      <PhotoUploadForm
        hospitalId={hospitalId}
        hospitalName={hospitalName}
        onUploaded={() => void refresh()}
      />

      {loadError && <p className="text-sm font-semibold text-red-700">{loadError}</p>}
      {loading ? (
        <p className="text-sm text-slate-500">사진 목록을 불러오는 중…</p>
      ) : photos.length === 0 ? (
        <p className="text-sm italic text-slate-500">
          아직 등록된 사진이 없습니다. 사진이 없어도 공개 페이지는 정보 중심으로 정상 노출됩니다.
        </p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {photos.map((photo) => (
            <PhotoRow
              key={photo.id}
              hospitalId={hospitalId}
              photo={photo}
              onChanged={() => void refresh()}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

function PhotoRow({
  hospitalId,
  photo,
  onChanged,
}: {
  hospitalId: string
  photo: PhotoSource
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const provenance = photo.photo_provenance ?? null
  const gate = describePhotoPublicGate(photo)
  const fileHref = photo.file_access_url ?? photo.file_url

  async function patchPublic(body: Record<string, unknown>, failure: string) {
    setBusy(true)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/${photo.id}/public`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      })
      onChanged()
    } catch {
      setError(safeOperatorError('onboarding', failure))
    } finally {
      setBusy(false)
    }
  }

  async function exclude() {
    if (!confirm('이 사진을 제외하시겠습니까? 병원 공개 페이지에서 빠집니다.')) return
    setBusy(true)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/essence/sources/${photo.id}/exclude`, {
        method: 'POST',
      })
      onChanged()
    } catch {
      setError(safeOperatorError('onboarding', '사진 목록을 다시 불러온 뒤 제외를 다시 누르세요.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="space-y-2 rounded-lg border border-slate-200 bg-white p-3 text-sm">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-slate-600">
            {photoTypeLabel(photo)}
          </span>
          <p className="mt-1 truncate font-medium text-slate-900" title={photo.title}>
            {fileHref ? (
              <a href={fileHref} target="_blank" rel="noopener" className="underline">
                {photo.title}
              </a>
            ) : (
              photo.title
            )}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void exclude()}
          disabled={busy}
          className="min-h-11 shrink-0 rounded border border-slate-300 bg-white px-3 text-xs font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          제외
        </button>
      </div>

      <label className="flex min-h-11 cursor-pointer items-center gap-2">
        <span
          className={`rounded-full px-2 py-1 text-xs font-semibold ${
            gate.state === 'PUBLIC'
              ? 'bg-blue-100 text-blue-700'
              : gate.state === 'BLOCKED_PROVENANCE'
                ? 'bg-amber-100 text-amber-800'
                : 'bg-slate-100 text-slate-600'
          }`}
        >
          {gate.badge}
        </span>
        <input
          type="checkbox"
          checked={photo.is_public}
          disabled={busy || !gate.canToggle}
          onChange={(e) =>
            void patchPublic(
              { is_public: e.target.checked },
              '사진 공개 여부를 다시 저장하세요.',
            )
          }
          aria-label={`${photo.title} 공개 페이지 표시`}
          className="rounded border-slate-300"
        />
        <span className="text-xs text-slate-600">
          {busy
            ? '저장 중…'
            : gate.reason
              ?? (photo.is_public ? '선택 해제하면 공개 중지' : '선택하면 다시 표시')}
        </span>
      </label>

      {provenance?.is_complete ? (
        <p className="text-xs text-slate-500">
          사용 권리 확인됨 · {provenance.source_owner} · {provenance.rights_basis_label}
          {provenance.verified_by ? ` · 확인 ${formatActorLabel(provenance.verified_by)}` : ''}
        </p>
      ) : (
        <PhotoRightsEntry
          photo={photo}
          busy={busy}
          onSubmit={(rights, makePublic) =>
            void patchPublic(
              {
                // 공개는 사람이 체크했을 때만 바뀐다 — 권리 기록을 남긴 것이 곧
                // 공개 결정은 아니다.
                ...(makePublic ? { is_public: true } : {}),
                photo_source_owner: rights.owner.trim(),
                photo_rights_basis: rights.basis,
                photo_evidence_reference: rights.reference.trim(),
              },
              '사진 사용 권리 정보를 확인한 뒤 다시 저장하세요.',
            )
          }
        />
      )}

      {error && <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-700">{error}</p>}
    </li>
  )
}

/** 권리 기록이 빈 사진만 다시 묻는다. 공개는 별도의 결정이므로 체크했을 때만 함께 바꾼다. */
function PhotoRightsEntry({
  photo,
  busy,
  onSubmit,
}: {
  photo: PhotoSource
  busy: boolean
  onSubmit: (rights: PhotoRightsDraft, makePublic: boolean) => void
}) {
  const provenance = photo.photo_provenance ?? null
  const [draft, setDraft] = useState<PhotoRightsDraft>({
    owner: provenance?.source_owner ?? '',
    basis: provenance?.rights_basis ?? '',
    reference: provenance?.evidence_reference ?? '',
  })
  const [makePublic, setMakePublic] = useState(false)

  return (
    <div className="space-y-2 rounded-lg border border-amber-200 bg-amber-50 p-3">
      <p className="text-xs leading-5 text-amber-900">
        {provenance?.missing_message
          ?? '이 사진을 공개하려면 소유자와 사용 근거를 먼저 입력해야 합니다.'}
      </p>
      <PhotoRightsFields
        idPrefix={`photo-${photo.id}`}
        value={draft}
        hospitalName={null}
        onChange={setDraft}
      />
      <label className="flex min-h-11 cursor-pointer items-center gap-2 text-xs text-amber-900">
        <input
          type="checkbox"
          checked={makePublic}
          onChange={(e) => setMakePublic(e.target.checked)}
          aria-label={`${photo.title} 저장과 함께 공개`}
          className="rounded border-slate-300"
        />
        저장과 함께 공개 페이지에 표시
      </label>
      <button
        type="button"
        disabled={busy || !photoRightsReady(draft)}
        onClick={() => onSubmit(draft, makePublic)}
        className="min-h-11 rounded-lg bg-blue-600 px-3 py-2 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
      >
        {busy ? '저장 중…' : makePublic ? '권리 정보 저장하고 공개' : '권리 정보 저장'}
      </button>
    </div>
  )
}

/**
 * 사진 업로드 — 유형·제목·권리 정보를 한 번에 받는다.
 *
 * 허용 용도는 유형에서 파생하므로 따로 묻지 않는다. 여러 장을 올릴 때는 파일마다
 * 제목을 받아 공개 페이지의 사진 설명이 반복되지 않게 한다.
 */
function PhotoUploadForm({
  hospitalId,
  hospitalName,
  onUploaded,
}: {
  hospitalId: string
  hospitalName: string | null
  onUploaded: () => void
}) {
  const [sourceType, setSourceType] = useState(PHOTO_SOURCE_TYPE_OPTIONS[0].value)
  const [files, setFiles] = useState<File[]>([])
  const [titles, setTitles] = useState<string[]>([])
  const [rights, setRights] = useState<PhotoRightsDraft>({ owner: '', basis: '', reference: '' })
  const [isPublic, setIsPublic] = useState(true)
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
      const body = photoUploadFormData({
        sourceType,
        title: titles[i] ?? '',
        file: files[i],
        isPublic,
        owner: rights.owner,
        basis: rights.basis,
        reference: rights.reference,
      })
      try {
        // 여러 장이면 마지막에 한 번만 재검증한다 — 장마다 돌리면 같은 일을 N번 한다.
        await fetchAPI(
          `/admin/hospitals/${hospitalId}/essence/sources/upload${
            files.length > 1 ? '?skip_revalidate=true' : ''
          }`,
          { method: 'POST', body },
        )
        succeeded += 1
      } catch {
        // 파일별 결과는 아래 요약 한 줄로 알린다.
      }
    }

    if (succeeded > 0 && files.length > 1) {
      try {
        await fetchAPI(`/admin/hospitals/${hospitalId}/essence/revalidate`, { method: 'POST' })
      } catch {
        // 재검증 실패는 다음 저장에서 회복된다.
      }
    }

    const failed = files.length - succeeded
    setFeedback(
      failed === 0
        ? `${succeeded}장 저장 완료.`
        : succeeded === 0
          ? safeOperatorError('onboarding', `${failed}장 저장 실패. 파일과 권리 정보를 확인한 뒤 다시 저장하세요.`)
          : `${succeeded}장 저장, ${failed}장 실패. 실패한 파일만 다시 선택해 저장하세요.`,
    )
    if (succeeded > 0) {
      selectFiles(null)
      const input = document.getElementById('info-photo-file') as HTMLInputElement | null
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
          aria-label="사진 유형"
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm"
        >
          {PHOTO_SOURCE_TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
        <p className="self-center text-xs leading-5 text-slate-600">
          여러 장을 한 번에 고를 수 있습니다. 제목은 공개 페이지의 사진 설명으로 쓰입니다.
        </p>
      </div>

      <PhotoRightsFields
        idPrefix="info-photo"
        value={rights}
        hospitalName={hospitalName}
        onChange={setRights}
      />

      <input
        id="info-photo-file"
        required
        type="file"
        multiple
        accept="image/*"
        onChange={(e) => selectFiles(e.target.files)}
        className="block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-1.5 file:text-sm"
      />

      {files.length > 0 && (
        <ul className="space-y-2">
          {files.map((file, index) => (
            <li key={`${file.name}-${index}`} className="grid gap-1 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] md:items-center">
              <label
                htmlFor={`info-photo-title-${index}`}
                className="truncate text-xs text-slate-500"
                title={file.name}
              >
                {file.name}
              </label>
              <input
                id={`info-photo-title-${index}`}
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

      <label className="flex min-h-11 items-center gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          checked={isPublic}
          onChange={(e) => setIsPublic(e.target.checked)}
          className="rounded border-slate-300"
        />
        업로드와 동시에 공개 페이지에 표시
      </label>

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy || files.length === 0 || !photoRightsReady(rights)}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? '저장 중…' : files.length > 1 ? `${files.length}장 저장` : '사진 저장'}
        </button>
        {feedback && <span className="text-xs text-slate-600">{feedback}</span>}
      </div>
    </form>
  )
}
