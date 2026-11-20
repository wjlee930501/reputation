import { innerKeepSegments, keepTogetherSegments, type TextSegment } from '@/lib/clinic-text'

function renderSegments(segments: TextSegment[], nested: boolean) {
  return segments.map((segment, index) =>
    segment.keep ? (
      <span key={index} className="clinic-keep">
        {nested ? segment.text : renderSegments(innerKeepSegments(segment.text), true)}
      </span>
    ) : (
      segment.text
    ),
  )
}

/**
 * 시간 범위·괄호·전화번호(제목이면 가운뎃점 낱말까지)를 한 줄에서 끊지 않고 그린다.
 * 글자는 바꾸지 않고 span으로만 감싼다 — 추출되는 문장은 원문과 같다(lib/clinic-text.ts).
 */
export function KeepTogether({ text, compounds = false }: { text: string; compounds?: boolean }) {
  return <>{renderSegments(keepTogetherSegments(text, { compounds }), false)}</>
}
