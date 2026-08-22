/**
 * 운영 기준 초안의 "항목별 근거 연결" 해석.
 *
 * 초안은 근거 노트를 UUID로만 참조한다. 화면이 그 UUID를 실제 노트로 바꾸지 못하면
 * 운영자는 항목마다 "근거 노트를 다시 불러와 확인이 필요합니다."만 보게 되는데,
 * 승인 체크리스트는 바로 그 연결이 맞는지 확인하라고 요구한다. 확인할 수 없는 것을
 * 확인했다고 체크하는 승인은 승인이 아니므로, 여기서 해석 결과와 차단 사유를 함께
 * 계산해 승인 버튼과 화면 문구가 같은 사실을 쓰게 한다.
 */

export interface EvidenceMapResolution {
  /** evidence_map이 참조하는 노트 참조의 총 개수 (중복 포함 — 화면 항목 수와 같다). */
  total: number
  /** 실제 노트로 해석된 참조 개수. */
  resolved: number
  /** 해석하지 못한 노트 ID (중복 제거). */
  missingIds: string[]
}

export function resolveEvidenceMap(
  evidenceMap: Record<string, unknown> | null | undefined,
  notesById: ReadonlyMap<string, unknown>,
): EvidenceMapResolution {
  let total = 0
  let resolved = 0
  const missing = new Set<string>()

  for (const value of Object.values(evidenceMap ?? {})) {
    if (!Array.isArray(value)) continue
    for (const noteId of value) {
      total += 1
      if (typeof noteId === 'string' && notesById.has(noteId)) {
        resolved += 1
      } else {
        missing.add(String(noteId))
      }
    }
  }

  return { total, resolved, missingIds: [...missing] }
}

export interface EvidenceApprovalState {
  resolution: EvidenceMapResolution
  /** 근거 노트를 아직 불러오는 중인가. */
  loading: boolean
  /** 노트를 불러오지 못한 자료가 있는가 (네트워크/서버 오류). */
  loadFailed: boolean
}

/**
 * 승인을 막아야 하는 이유. 비어 있으면 근거 확인이 가능한 상태다.
 *
 * 문구는 그대로 화면에 나가므로 다음 행동까지 적는다 — "실패했습니다"만으로는
 * 운영자가 무엇을 해야 할지 알 수 없다.
 */
export function evidenceApprovalBlockers(state: EvidenceApprovalState): string[] {
  const blockers: string[] = []
  if (state.loading) {
    blockers.push('근거 노트를 불러오는 중입니다. 완료된 뒤 승인하세요.')
    return blockers
  }
  if (state.loadFailed) {
    blockers.push('근거 노트를 불러오지 못했습니다. [근거 노트 다시 불러오기]로 다시 시도한 뒤 승인하세요.')
  }
  const missingCount = state.resolution.missingIds.length
  if (!state.loadFailed && missingCount > 0) {
    blockers.push(
      `초안이 참조하는 근거 노트 ${missingCount}개가 현재 자료에 없습니다. ` +
        '자료를 다시 처리했다면 자료를 선택해 초안을 새로 만드세요.',
    )
  }
  return blockers
}

/** 근거 연결 패널 상단에 쓸 한 줄 요약. */
export function evidenceResolutionSummary(state: EvidenceApprovalState): string {
  if (state.resolution.total === 0) return '연결된 근거 노트가 없습니다.'
  if (state.loading) return `근거 노트 불러오는 중 (${state.resolution.total}개 항목)`
  if (state.loadFailed) return `근거 노트를 불러오지 못했습니다 (${state.resolution.total}개 항목)`
  return `${state.resolution.resolved}/${state.resolution.total}개 항목이 근거 노트와 연결됨`
}
