/**
 * 병원 사이트 줄바꿈 묶음 — 한 줄에서 끊기면 읽기 어려운 조각을 찾아 화면에서만 묶는다.
 *
 * 2026-11-20 마케팅 점검: keep-all로 한글 단어 가운데는 끊기지 않지만, 띄어쓰기에서는 끊긴다.
 * 그래서 '09:00 ~ 21:00 (점심 13:30 ~ / 14:30)', '주말· / 공휴일에도'처럼 시간 범위·괄호·
 * 가운뎃점 낱말이 중간에서 갈렸다. 여기서는 텍스트를 조각으로 나누기만 하고(순수 함수),
 * 묶은 조각은 화면이 inline-block 한 칸으로 그린다(`KeepTogether`) — 줄에 들어가면 통째로
 * 다음 줄로 넘어가고, 칸보다 길면(반 폭 칸의 긴 괄호) 넘치지 않고 그 안에서만 줄을 바꾼다.
 *
 * 데이터는 바꾸지 않는다 — business_hours는 JSON-LD(openingHours)·llms.txt에도 그대로 나가므로
 * NBSP 같은 글자를 넣지 않는다.
 */

export interface TextSegment {
  text: string
  /** true면 한 줄에서 끊지 않는다. */
  keep: boolean
}

/** 묶을 수 있는 조각의 최대 길이(글자). 이보다 긴 조각은 묶어도 어차피 안에서 줄이 바뀐다. */
export const KEEP_MAX_CHARS = 40

const TIME = String.raw`\d{1,2}:\d{2}`
const RANGE_SEP = String.raw`\s*[~〜～\-–—]\s*`
const TIME_RANGE = String.raw`${TIME}${RANGE_SEP}${TIME}`
// 대표번호(02-123-4567, 053-801-3007, 010-1234-5678)와 1588-1234 같은 전국 번호.
const PHONE = String.raw`(?:0\d{1,2}[-.\s]\d{3,4}[-.\s]\d{4}|1\d{3}-\d{4})`
// 가운뎃점으로 이은 짧은 낱말('주말·공휴일에도'). 제목처럼 큰 글자에서만 쓴다.
const COMPOUND = String.raw`[가-힣A-Za-z0-9]+(?:·[가-힣A-Za-z0-9]+)+`

function pushSegment(segments: TextSegment[], text: string, keep: boolean) {
  if (!text) return
  const last = segments[segments.length - 1]
  if (last && !last.keep && !keep) {
    last.text += text
    return
  }
  segments.push({ text, keep })
}

/** 괄호 밖 글에서 시간 범위·전화번호(·가운뎃점 낱말)를 묶는다. */
function segmentPlain(text: string, compounds: boolean, segments: TextSegment[]) {
  // 바로 뒤의 쉼표·마침표는 묶음에 붙인다 — 묶음 뒤에서 줄이 바뀌면 ','만 다음 줄로 떨어졌다.
  const pattern = new RegExp(`(?:${TIME_RANGE}|${PHONE}${compounds ? `|${COMPOUND}` : ''})[,.]?`, 'g')
  let cursor = 0
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0
    const keep = match[0].length <= KEEP_MAX_CHARS
    pushSegment(segments, text.slice(cursor, start), false)
    pushSegment(segments, match[0], keep)
    cursor = start + match[0].length
  }
  pushSegment(segments, text.slice(cursor), false)
}

/**
 * 괄호 묶음 — '(점심 13:00 ~ 14:00)'처럼 한 말이면 통째로, 쉼표로 여러 말이 든 괄호
 * ('(점심 13:00 ~ 14:00, 접수 마감 18:00)')는 쉼표 뒤에서만 끊기게 말마다 묶는다.
 */
function segmentParenthetical(group: string, segments: TextSegment[]) {
  if (group.length <= KEEP_MAX_CHARS && !group.includes(',')) {
    pushSegment(segments, group, true)
    return
  }
  // 쉼표 뒤 공백이 없어도('…14:00,접수 마감') 쉼표 뒤에서 나눈다. 원문 글자는 그대로 둔다.
  const clauses = group.split(/(?<=,)(\s*)/).filter(Boolean)
  for (const clause of clauses) {
    if (/^\s+$/.test(clause)) pushSegment(segments, clause, false)
    else if (clause.length <= KEEP_MAX_CHARS) pushSegment(segments, clause, true)
    else segmentPlain(clause, false, segments)
  }
}

export function keepTogetherSegments(text: string, options: { compounds?: boolean } = {}): TextSegment[] {
  const segments: TextSegment[] = []
  const compounds = options.compounds ?? false
  let cursor = 0
  // 괄호 바로 뒤의 쉼표·마침표도 묶음에 붙인다 — '(점심시간 없음).'의 '.'만 다음 줄로 떨어지지 않게.
  for (const match of text.matchAll(/\([^()]*\)[,.]?/g)) {
    const start = match.index ?? 0
    segmentPlain(text.slice(cursor, start), compounds, segments)
    segmentParenthetical(match[0], segments)
    cursor = start + match[0].length
  }
  segmentPlain(text.slice(cursor), compounds, segments)
  return segments
}

/**
 * 묶음 안쪽 — 괄호 묶음이 반 폭 칸보다 넓어 안에서 줄이 바뀔 때도 시간 범위·전화번호는 갈리지
 * 않게 한 겹 더 나눈다('(점심 / 13:00 ~ 14:00,'은 되고 '(점심 13:00 ~ / 14:00,'은 안 된다).
 * 묶음 전체가 이미 시간 범위 하나면 더 나누지 않는다.
 */
export function innerKeepSegments(text: string): TextSegment[] {
  const segments: TextSegment[] = []
  segmentPlain(text, false, segments)
  return segments.length === 1 ? [{ text, keep: false }] : segments
}
