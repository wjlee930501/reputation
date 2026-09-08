'use client'

export const DOCTOR_ASSET_KIND_OPTIONS = [
  { value: 'VERIFIED_REAL_PERSON', label: '실제 원장 사진 — 본인 확인 완료' },
  { value: 'EDITORIAL_GRAPHIC', label: '캐릭터·일러스트 — 의료진 영역 사용 안 함' },
]

export const FACILITY_ASSET_KIND_OPTIONS = [
  { value: 'VERIFIED_FACILITY', label: '실제 병원 공간 사진 — 장소 확인 완료' },
  { value: 'EDITORIAL_GRAPHIC', label: '생성·일러스트 이미지 — 콘텐츠 전용' },
]

export function photoAssetKindOptions(sourceType: string) {
  return sourceType === 'PHOTO_DOCTOR'
    ? DOCTOR_ASSET_KIND_OPTIONS
    : FACILITY_ASSET_KIND_OPTIONS
}

// 공개되는 사진에는 권리 근거가 있어야 저장된다. 값은 서버가 허용하는 두 가지뿐이다.
export const PHOTO_RIGHTS_BASIS_OPTIONS = [
  { value: 'LICENSE', label: '라이선스 보유 — 촬영·구매 계약이 있음' },
  { value: 'OWNER_CONSENT', label: '촬영 대상·소유자 동의를 받음' },
]

export interface PhotoRightsDraft {
  owner: string
  basis: string
  reference: string
}

export function photoRightsReady(value: PhotoRightsDraft): boolean {
  return Boolean(value.owner.trim() && value.basis && value.reference.trim())
}

export function PhotoRightsFields({
  idPrefix,
  value,
  hospitalName,
  onChange,
}: {
  idPrefix: string
  value: PhotoRightsDraft
  hospitalName: string | null
  onChange: (value: PhotoRightsDraft) => void
}) {
  return (
    <div className="grid gap-2 rounded-lg bg-slate-50 p-3 md:grid-cols-2">
      <input
        id={`${idPrefix}-rights-owner`}
        required
        value={value.owner}
        onChange={(event) => onChange({ ...value, owner: event.target.value })}
        placeholder={hospitalName ? `사진 소유자 (예: ${hospitalName})` : '사진 소유자'}
        aria-label={`${idPrefix} 사진 소유자 예외`}
        className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs"
      />
      <select
        id={`${idPrefix}-rights-basis`}
        required
        value={value.basis}
        onChange={(event) => onChange({ ...value, basis: event.target.value })}
        aria-label={`${idPrefix} 사진 사용 권리 근거 예외`}
        className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs"
      >
        <option value="">권리 근거 선택</option>
        {PHOTO_RIGHTS_BASIS_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
      <input
        id={`${idPrefix}-rights-evidence`}
        required
        value={value.reference}
        onChange={(event) => onChange({ ...value, reference: event.target.value })}
        placeholder="증빙 위치 (계약 조항, 동의서 파일명 등)"
        aria-label={`${idPrefix} 사진 사용 권리 증빙 위치 예외`}
        className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs md:col-span-2"
      />
    </div>
  )
}
