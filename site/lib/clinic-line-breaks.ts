/**
 * 섹션 설정의 줄바꿈 지정(`hero.line_breaks`, 설계 3.3) — PC·휴대폰마다 운영자가 정한 줄을 원문 한 벌에 `<br>`로 끼운다.
 *
 * 공개 응답은 원문과 글자·띄어쓰기가 맞는 줄만 싣는다(백엔드 `site_config_public` + `lines_match`). 화면은 원문 글을
 * **한 번만** 그리고, 줄이 바뀌는 자리에 그 기기에서만 보이는 `<br>`을 둔다 — PC·휴대폰 글을 따로 두 벌 그리면
 * 답변 엔진·복사·화면 낭독이 같은 글을 두 번 읽는다. 줄 경계가 원문의 공백 자리이면 공백은 그대로 두고 그 뒤에
 * `<br>`을 둔다(줄 끝 공백은 화면에서 보이지 않고, 그 기기에서 줄을 나누지 않을 때는 띄어쓰기로 남는다).
 */

export type LineBreakDevice = 'pc' | 'mobile' | 'both'

export interface LineBreakSpec {
  pc?: string | null
  mobile?: string | null
}

// 백엔드(Python `str.split()`·`str.strip()`)가 공백으로 보는 글자. JavaScript `\s`와 달리 `\x1c`~`\x1f`·NEL(`\x85`)이
// 들고 BOM(`\ufeff`)은 빠진다 — 같지 않으면 백엔드가 맞다고 본 줄을 사이트가 버린다.
const PY_SPACE = '\\t\\n\\v\\f\\r \\x1c-\\x1f\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000'
const SPACES = new RegExp(`[${PY_SPACE}]+`)
const EDGE_SPACES = new RegExp(`^[${PY_SPACE}]+|[${PY_SPACE}]+$`, 'g')
// Python `str.splitlines()`의 줄 경계.
const LINE_BOUNDARY = /\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029]/

/** 공백·줄바꿈을 공백 하나로 이은 한 줄(백엔드 `joined`). */
export function joinedText(text: string): string {
  return text.split(SPACES).filter(Boolean).join(' ')
}

/** 백엔드 `split_lines`와 같다 — `splitlines()`는 끝 줄 경계 뒤에 빈 줄을 만들지 않는다. */
function splitLines(value: string): string[] {
  const lines = value.split(LINE_BOUNDARY)
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop()
  const trimmed = lines.map((line) => line.replace(EDGE_SPACES, ''))
  return value === '' ? [''] : trimmed
}

/**
 * 줄 나눈 글(`lines`)이 원문과 맞으면 `<br>`을 넣을 자리(원문을 이은 한 줄 기준의 글자 위치)를, 맞지 않으면 null.
 * 백엔드 `lines_match`와 같은 규칙이다 — 줄 경계는 원문 공백 한 칸 자리(그 공백 뒤)이거나 단어 중간이다.
 */
export function lineBreakOffsets(source: string, lines: string): number[] | null {
  const target = joinedText(source)
  const parts = splitLines(lines).map(joinedText)
  const offsets: number[] = []
  let position = 0
  for (let index = 0; index < parts.length; index += 1) {
    const line = parts[index]
    if (!line || !target.startsWith(line, position)) return null
    position += line.length
    if (index < parts.length - 1) {
      if (position < target.length && target[position] === ' ') position += 1
      offsets.push(position)
    }
  }
  return position === target.length ? offsets : null
}

/** PC·휴대폰 줄바꿈을 합친 자리표(위치 → 어느 기기에서 줄을 나누는지). 맞지 않는 기기는 버린다. */
export function lineBreakMap(source: string | null | undefined, spec: LineBreakSpec | null | undefined): Map<number, LineBreakDevice> {
  const map = new Map<number, LineBreakDevice>()
  if (!source || !spec) return map
  const pc = spec.pc ? lineBreakOffsets(source, spec.pc) : null
  const mobile = spec.mobile ? lineBreakOffsets(source, spec.mobile) : null
  for (const at of pc ?? []) map.set(at, 'pc')
  for (const at of mobile ?? []) map.set(at, map.get(at) === 'pc' ? 'both' : 'mobile')
  return map
}

/** 그릴 조각 — 글 조각 또는 그 기기에서만 보이는 줄바꿈. */
export type LineBreakPiece = { kind: 'text'; text: string } | { kind: 'br'; device: LineBreakDevice }

/**
 * 원문을 이은 한 줄의 `[start, start + text.length)` 구간 글(`raw`, 이은 모양으로 바꿔 쓴다)을 줄바꿈 자리에서 나눈다. 여러 조각(첫 화면 제목의
 * 앞 조각·강조 조각)으로 나뉜 글은 조각마다 자기 시작 위치를 넘기고, 앞 조각과 같은 줄에 이어지는 조각은 `breakAtStart`.
 */
export function lineBreakPieces(
  raw: string,
  start: number,
  breaks: Map<number, LineBreakDevice>,
  breakAtStart = false,
): LineBreakPiece[] {
  if (breaks.size === 0) return [{ kind: 'text', text: raw }]
  // 자리는 이은 한 줄 기준이다 — 원문에 줄바꿈·겹공백이 있으면 이은 모양으로 잘라야 자리가 맞는다(화면에서도 공백 하나로 보인다).
  const text = joinedText(raw)
  const pieces: LineBreakPiece[] = []
  let cursor = 0
  // 조각 시작 자리의 줄바꿈은 그 조각이 앞 조각과 같은 줄에 이어질 때(`breakAtStart`)만 넣는다 — 새 줄에서 시작하는
  // 조각(블록)이면 이미 다른 줄이다.
  const cuts = [...breaks.keys()]
    .filter((at) => (breakAtStart ? at >= start && at > 0 : at > start) && at < start + text.length)
    .sort((a, b) => a - b)
  for (const at of cuts) {
    const local = at - start
    if (local > cursor) pieces.push({ kind: 'text', text: text.slice(cursor, local) })
    pieces.push({ kind: 'br', device: breaks.get(at) as LineBreakDevice })
    cursor = local
  }
  if (cursor < text.length) pieces.push({ kind: 'text', text: text.slice(cursor) })
  return pieces
}

/** 조각(첫 화면 제목의 앞 조각들·강조 조각)을 공백으로 이은 글에서 각 조각이 시작하는 자리. 조각 글은 이은 모양으로. */
export function partStarts(parts: readonly string[]): Array<{ text: string; start: number }> {
  let start = 0
  return parts.map((part) => {
    const text = joinedText(part)
    const entry = { text, start }
    start += text.length + 1
    return entry
  })
}
