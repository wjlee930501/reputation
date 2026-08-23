/**
 * 사진이 공개 표면에 나갈 수 있는 조건.
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
  '사용 권리 기록(소유자·권리 근거·증빙 위치·확인 담당자)이 모두 있는 사진만 공개할 수 있습니다. 자료 처리 상태와는 무관합니다. 의료광고법 우려 카테고리(환자 후기·전후 사진)는 애초에 등록할 수 없습니다.'

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
  return { state: 'PRIVATE_READY', badge: '비공개', canToggle: true, reason: null }
}
