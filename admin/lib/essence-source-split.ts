/**
 * 근거 자료와 사진 자산을 나눈다.
 *
 * 사진은 공개 표면에 쓰는 자산이고, 본문 근거를 뽑는 대상이 아니다. 백엔드도 그렇게
 * 본다 — 승인 게이트(`essence.py`)와 월간 완결성 집계(`essence_engine.py`)는
 * `PHOTO_*`를 필수 자료 분모에서 뺀다. 그런데 운영 기준 화면은 사진을 근거 추출 표에
 * 같이 세워 두고 "처리된 자료 3 / 12"의 분모에도 넣었다. 그래서 사진을 여러 장 올린
 * 병원은 아무리 자료를 처리해도 분모를 채울 수 없고, 사진 행의 [근거 추출] 버튼은
 * 눌러도 400으로 실패했다.
 *
 * 화면이 백엔드와 같은 분모를 쓰게 하고, 사진은 사진으로 센다.
 */

export interface EssenceSourceLike {
  source_type: string
  status?: string
  evidence_note_count?: number | null
}

export function isPhotoSource(source: EssenceSourceLike): boolean {
  return typeof source.source_type === 'string' && source.source_type.startsWith('PHOTO_')
}

export interface EssenceSourceSplit<T extends EssenceSourceLike> {
  /** 근거 추출 대상 — 사진이 아닌 자료 */
  textSources: T[]
  /** 공개 표면용 사진 자산 */
  photoSources: T[]
  /** 근거 추출을 마친 자료 수 (사진 제외) */
  processedTextCount: number
  /** 근거 추출 대상 자료 수 (사진 제외, 제외 처리한 자료 포함) */
  textSourceCount: number
  /** 근거 노트 합계 (사진 제외) */
  evidenceNoteCount: number
}

export function splitEssenceSources<T extends EssenceSourceLike>(
  sources: T[],
): EssenceSourceSplit<T> {
  const rows = Array.isArray(sources) ? sources : []
  const textSources = rows.filter((source) => !isPhotoSource(source))
  const photoSources = rows.filter((source) => isPhotoSource(source))

  return {
    textSources,
    photoSources,
    processedTextCount: textSources.filter((source) => source.status === 'PROCESSED').length,
    textSourceCount: textSources.length,
    evidenceNoteCount: textSources.reduce(
      (sum, source) => sum + (source.evidence_note_count ?? 0),
      0,
    ),
  }
}

/** 사진이 근거 표에서 빠졌다는 사실을 화면이 직접 말한다. */
export function describePhotoSourceExclusion(photoCount: number): string | null {
  if (photoCount <= 0) return null
  return `사진 ${photoCount}장은 공개 표면용 자산이라 이 표와 처리 집계에서 제외했습니다. 온보딩 화면에서 관리합니다.`
}
