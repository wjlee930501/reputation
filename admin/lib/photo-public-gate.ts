/**
 * 사진이 병원 공개 페이지에 나갈 수 있는 조건.
 *
 * Wiki 화면은 "검수 완료된 사진만 토글로 공개"라고 적었지만, 실제 게이트는 자료의
 * 처리 상태(`PROCESSED`)가 아니다. 공개 API와 DB 제약(`ck_public_photo_requires_provenance`)이
 * 요구하는 것은 **사용 권리 기록**이다 — 소유자, 권리 근거, 증빙 위치, 확인 담당자,
 * 확인 시각. 그래서 상태가 "대기"인 사진도 권리 기록이 있으면 공개되고, "처리완료"인
 * 사진도 권리 기록이 없으면 공개되지 않는다.
 *
 * 문구가 없는 조건을 말하면 운영자는 사진을 공개하려고 근거 추출을 반복하고(사진은
 * 애초에 근거 추출 대상이 아니라 실패한다), 정작 채워야 하는 권리 정보는 비워 둔다.
 */

export const PHOTO_PUBLIC_GATE_COPY =
  '소유자·권리 근거·증빙 위치를 확인해 업로드한 사진은 병원 사이트에 바로 표시됩니다. 권리 기록이 불완전한 사진은 공개되지 않으며, 필요할 때만 여기서 공개를 중지하거나 다시 표시합니다. 자료 처리 상태와는 무관합니다. 의료광고법 우려 카테고리(환자 후기·전후 사진)는 애초에 등록할 수 없습니다.'

export interface PhotoGateSourceLike {
  status?: string
  is_public: boolean
  file_url?: string | null
  photo_provenance?: { is_complete: boolean; missing_message?: string | null } | null
}

export type PhotoGateState =
  /** 지금 공개 중 */
  | 'PUBLIC'
  /** 공개할 수 있지만 비공개로 두었다 */
  | 'PRIVATE_READY'
  /** 권리 기록이 없어 공개할 수 없다 */
  | 'BLOCKED_PROVENANCE'
  /** 제외 처리해 공개 대상이 아니다 */
  | 'EXCLUDED'

export interface PhotoGate {
  state: PhotoGateState
  /** 배지 문구 — 토글 라벨이 아니라 현재 상태를 말한다 */
  badge: string
  /** 토글을 누를 수 있는가 */
  canToggle: boolean
  /** 누를 수 없을 때 그 이유 */
  reason: string | null
}

export function describePhotoPublicGate(source: PhotoGateSourceLike): PhotoGate {
  if (source.status === 'EXCLUDED') {
    return {
      state: 'EXCLUDED',
      badge: '제외',
      canToggle: false,
      reason: '제외 처리한 사진은 공개할 수 없습니다. 자료 화면에서 제외를 해제하세요.',
    }
  }

  const provenanceComplete = source.photo_provenance?.is_complete ?? false

  if (source.is_public) {
    return { state: 'PUBLIC', badge: '공개 중', canToggle: true, reason: null }
  }
  if (!provenanceComplete) {
    return {
      state: 'BLOCKED_PROVENANCE',
      badge: '공개 불가 · 사용 권리 미기록',
      canToggle: false,
      reason:
        source.photo_provenance?.missing_message
        ?? '공개하려면 사진 사용 권리 정보가 필요합니다.',
    }
  }
  // 완전한 권리 기록을 갖춘 신규 업로드는 서버가 즉시 공개한다. 따라서 이 상태는
  // 업로드 뒤 공개 클릭을 기다리는 상태가 아니라, 운영자가 나중에 공개를 중지한 상태다.
  return { state: 'PRIVATE_READY', badge: '공개 중지됨', canToggle: true, reason: null }
}

/**
 * 의료진에 연결한 사진이 공개 페이지에 실제로 나가는가.
 *
 * 사진 공개 토글만으로는 부족하다. 공개 API(`public/physicians.doctor_identity_asset_ids`)는
 * 원장 사진(`PHOTO_DOCTOR`) 중 공개·미제외·파일 있음에 더해 **실제 인물로 분류**
 * (`asset_kind=VERIFIED_REAL_PERSON`, 용도 `DOCTOR_IDENTITY`)된 사진만 의료진 얼굴로 쓰고,
 * 나머지는 이름 첫 글자를 보인다. 분류가 없는 옛 사진은 서버가 일러스트로 읽는다
 * (`photo_assets.legacy_photo_asset_kind`). 어드민이 「사진으로 사용 중」만 말하면 AE는
 * 공개 페이지의 첫 글자가 오류인 줄 안다(2026-11-20 행복드림 — 원장 캐릭터 일러스트).
 */
export interface DoctorPhotoSourceLike {
  source_type: string
  status?: string
  is_public: boolean
  file_url?: string | null
  source_metadata?: { asset_kind?: unknown; approved_usage?: unknown } | null
  photo_provenance?: { is_complete: boolean } | null
}

export type DoctorPhotoBlocker =
  | 'EXCLUDED'
  | 'NOT_CLASSIFIED'
  | 'NOT_REAL_PERSON'
  | 'NO_FILE'
  | 'NO_RIGHTS'
  | 'NOT_PUBLIC'

/** 공개 페이지에 의료진 사진으로 나가지 못하는 첫 이유. 나가면 null. */
export function doctorPhotoPublicBlocker(source: DoctorPhotoSourceLike): DoctorPhotoBlocker | null {
  if (source.status === 'EXCLUDED') return 'EXCLUDED'
  if (source.source_type !== 'PHOTO_DOCTOR') return 'NOT_REAL_PERSON'
  const kind = source.source_metadata?.asset_kind
  if (typeof kind !== 'string') return 'NOT_CLASSIFIED'
  const usage = source.source_metadata?.approved_usage
  if (kind !== 'VERIFIED_REAL_PERSON' || !Array.isArray(usage) || !usage.includes('DOCTOR_IDENTITY')) {
    return 'NOT_REAL_PERSON'
  }
  if (!source.file_url) return 'NO_FILE'
  if (!source.is_public) return source.photo_provenance?.is_complete ? 'NOT_PUBLIC' : 'NO_RIGHTS'
  return null
}

const DOCTOR_PHOTO_BLOCKER_REASON: Record<DoctorPhotoBlocker, string> = {
  EXCLUDED: '제외한 사진입니다. 사진을 다시 고르세요',
  NOT_CLASSIFIED: '실제 원장 사진인지 확인되지 않은 예전 사진입니다. 실제 원장 사진을 새로 올려 연결하세요',
  NOT_REAL_PERSON: '캐릭터·일러스트로 분류된 사진입니다. 실제 원장 사진을 올려 연결하세요',
  NO_FILE: '사진 파일이 없습니다. 사진을 다시 올리세요',
  NO_RIGHTS: '사용 권리 기록이 없어 공개되지 않는 사진입니다. 「사진과 사용 권리」에서 사용 권리를 입력하세요',
  NOT_PUBLIC: '공개를 중지한 사진입니다. 「사진과 사용 권리」에서 다시 표시하세요',
}

/** 연결은 되어 있지만 공개 페이지에는 사진 대신 이름 첫 글자가 나간다는 안내. */
export function doctorPhotoBlockerMessage(blocker: DoctorPhotoBlocker): string {
  return `공개 페이지에는 이 사진 대신 이름 첫 글자가 나갑니다 — ${DOCTOR_PHOTO_BLOCKER_REASON[blocker]}.`
}
