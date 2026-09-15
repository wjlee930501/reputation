'use client'

import {
  PHYSICIAN_TITLE_OPTIONS,
  joinLines,
  movePhysician,
  parseCommaList,
  parseLines,
  removePhysician,
} from '@/lib/physicians'
import type { DoctorPhotoOption } from '@/lib/physicians'
import type { Physician, PhysicianCredentials } from '@/types'

const FIELD_CLASS =
  'w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent'

function credentialsOf(row: Physician): PhysicianCredentials {
  return row.credentials ?? {}
}

/**
 * 반복 입력하는 의료진 목록.
 *
 * 원장명·약력 한 쌍 대신 이 목록이 사실의 원본이다 — 대표로 표시한 의료진에서 서버가
 * 원장 표시값을 파생한다. 진료 철학은 병원 단위 값이라 여기 있지 않다.
 */
export function PhysiciansSection({
  rows,
  photoOptions,
  photoOptionsError,
  onChange,
}: {
  rows: Physician[]
  photoOptions: DoctorPhotoOption[]
  photoOptionsError: string | null
  onChange: (rows: Physician[]) => void
}) {
  function update(index: number, patch: Partial<Physician>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  function updateCredentials(index: number, patch: Partial<PhysicianCredentials>) {
    const current = credentialsOf(rows[index])
    update(index, { credentials: { ...current, ...patch } })
  }

  return (
    <div className="space-y-3">
      {rows.map((row, i) => {
        const credentials = credentialsOf(row)
        return (
          <div key={i} className="space-y-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
            <div className="flex items-start justify-between gap-2">
              <span className="text-xs font-semibold text-slate-600">의료진 {i + 1}</span>
              <div className="flex shrink-0 items-center gap-1">
                <button
                  type="button"
                  onClick={() => onChange(movePhysician(rows, i, -1))}
                  disabled={i === 0}
                  aria-label={`의료진 ${i + 1} 표시 순서 위로`}
                  className="min-h-9 rounded border border-slate-300 bg-white px-2 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={() => onChange(movePhysician(rows, i, 1))}
                  disabled={i === rows.length - 1}
                  aria-label={`의료진 ${i + 1} 표시 순서 아래로`}
                  className="min-h-9 rounded border border-slate-300 bg-white px-2 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-40"
                >
                  ↓
                </button>
                <button
                  type="button"
                  onClick={() => onChange(removePhysician(rows, i))}
                  aria-label={`의료진 ${i + 1} 제거`}
                  className="min-h-9 px-2 text-lg leading-none text-slate-400 transition-colors hover:text-red-500"
                >
                  ×
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <input
                type="text"
                value={row.name}
                onChange={(e) => update(i, { name: e.target.value })}
                placeholder="이름 (예: 김민수)"
                aria-label={`의료진 ${i + 1} 이름`}
                className={FIELD_CLASS}
              />
              <input
                type="text"
                list="physician-title-options"
                value={row.title ?? ''}
                onChange={(e) => update(i, { title: e.target.value })}
                placeholder="직함 (예: 대표원장)"
                aria-label={`의료진 ${i + 1} 직함`}
                className={FIELD_CLASS}
              />
            </div>

            <input
              type="text"
              value={(row.specialties ?? []).join(', ')}
              onChange={(e) => update(i, { specialties: parseCommaList(e.target.value) })}
              placeholder="전문과목 (쉼표로 구분: 외과, 대장항문외과)"
              aria-label={`의료진 ${i + 1} 전문과목`}
              className={FIELD_CLASS}
            />

            <textarea
              value={row.career ?? ''}
              onChange={(e) => update(i, { career: e.target.value })}
              rows={3}
              placeholder="약력 — 확인한 내용만 적습니다."
              aria-label={`의료진 ${i + 1} 약력`}
              className={`${FIELD_CLASS} resize-none`}
            />

            <details className="rounded-lg border border-slate-200 bg-white p-3">
              <summary className="cursor-pointer text-xs font-semibold text-slate-700">
                자격·경력 상세
              </summary>
              <div className="mt-3 space-y-3">
                <input
                  type="text"
                  value={credentials.medical_school ?? ''}
                  onChange={(e) => updateCredentials(i, { medical_school: e.target.value })}
                  placeholder="의과대학"
                  aria-label={`의료진 ${i + 1} 의과대학`}
                  className={FIELD_CLASS}
                />
                <textarea
                  value={joinLines(credentials.board_certifications)}
                  onChange={(e) =>
                    updateCredentials(i, { board_certifications: parseLines(e.target.value) })
                  }
                  rows={2}
                  placeholder="전문의 자격 — 한 줄에 하나"
                  aria-label={`의료진 ${i + 1} 전문의 자격`}
                  className={`${FIELD_CLASS} resize-none`}
                />
                <textarea
                  value={joinLines(credentials.society_memberships)}
                  onChange={(e) =>
                    updateCredentials(i, { society_memberships: parseLines(e.target.value) })
                  }
                  rows={2}
                  placeholder="학회 — 한 줄에 하나"
                  aria-label={`의료진 ${i + 1} 학회`}
                  className={`${FIELD_CLASS} resize-none`}
                />
                <input
                  type="text"
                  value={credentials.license_number ?? ''}
                  onChange={(e) => updateCredentials(i, { license_number: e.target.value })}
                  placeholder="면허번호"
                  aria-label={`의료진 ${i + 1} 면허번호`}
                  className={FIELD_CLASS}
                />
              </div>
            </details>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <select
                  value={row.photo_source_id ?? ''}
                  onChange={(e) => update(i, { photo_source_id: e.target.value || null })}
                  aria-label={`의료진 ${i + 1} 사진`}
                  className={FIELD_CLASS}
                >
                  <option value="">사진 없음</option>
                  {photoOptions.map((option) => (
                    <option key={option.id} value={option.id}>{option.title}</option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-slate-500">
                  {photoOptionsError
                    ?? '아래 사진 섹션에 올린 원장 사진 중에서 고릅니다.'}
                </p>
              </div>
              <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={row.is_representative}
                  onChange={(e) => update(i, { is_representative: e.target.checked })}
                  aria-label={`의료진 ${i + 1} 대표`}
                  className="rounded border-slate-300"
                />
                대표 — 여러 명을 지정할 수 있습니다
              </label>
            </div>
          </div>
        )
      })}

      <datalist id="physician-title-options">
        {PHYSICIAN_TITLE_OPTIONS.map((title) => (
          <option key={title} value={title} />
        ))}
      </datalist>

      {rows.length === 0 && (
        <p className="text-sm text-slate-400">의료진을 추가해 주세요.</p>
      )}
    </div>
  )
}
