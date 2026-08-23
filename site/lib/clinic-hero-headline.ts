import type { ClinicAccessMode } from './clinic-design.ts'

/**
 * 첫 화면 제목의 조각들.
 *
 * P-A-4 — 이전 구현은 조각마다 `<span>`을 만들고 그 사이에 아무 문자도 넣지 않았다.
 * 화면에서는 CSS가 조각을 블록으로 쌓아 줄바꿈처럼 보였지만, 텍스트로 읽으면
 * `대장항문외과,의료진과 진료 정보를방문 전에 확인하세요`처럼 단어가 붙는다.
 * 이 화면의 목적이 답변 엔진에 병원을 정확히 읽히는 것이므로, 붙은 단어는
 * 시각 문제가 아니라 내용 문제다. 조각 사이 공백은 여기서 보장한다.
 *
 * 기본 문구를 세 조각으로 쪼개 두면 조각 수가 곧 줄 수가 되어, 긴 병원명이나
 * 짧은 진료과에서도 항상 세 줄이 나왔다. 기본 문구는 두 조각만 만들고 줄바꿈은
 * 브라우저가 폭에 맞춰 결정한다.
 */
export interface ClinicHeroHeadline {
  /** 강조 앞에 오는 조각들. 없을 수 있다. */
  lead: string[]
  /** 브랜드 색으로 강조하는 마지막 조각. 항상 값이 있다. */
  emphasis: string
  /** 조각을 공백으로 이은 한 문장. 접근성 이름·복사·크롤러가 읽는 값이다. */
  text: string
}

const MAX_PARTS = 3

function defaultParts(
  accessMode: ClinicAccessMode,
  specialtyLabel: string,
  hospitalName: string,
): string[] {
  const subject = specialtyLabel ? `${specialtyLabel},` : hospitalName
  if (accessMode === 'urgent') {
    return [subject, '오늘 진료시간과 위치를 방문 전에 확인하세요']
  }
  if (accessMode === 'specialist') {
    return [subject, '진료 분야와 의료진 정보를 차분히 확인하세요']
  }
  return [subject, '의료진과 진료 정보를 방문 전에 확인하세요']
}

export function buildClinicHeroHeadline(input: {
  approvedHeadline?: string | null
  accessMode: ClinicAccessMode
  specialtyLabel: string
  hospitalName: string
}): ClinicHeroHeadline {
  const approved = (input.approvedHeadline ?? '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, MAX_PARTS)

  const parts = approved.length > 0
    ? approved
    : defaultParts(input.accessMode, input.specialtyLabel, input.hospitalName)

  const emphasis = parts[parts.length - 1]
  const lead = parts.slice(0, -1)
  return { lead, emphasis, text: parts.join(' ') }
}
