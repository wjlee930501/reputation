/**
 * 섹션 머리의 영문 소제목(섹션 설정 `sections[].label`, ③-2c). 제목(h2) 밖의 장식이라 읽지 않는다 —
 * 제목 글자는 그대로다(C안 `SectionTitle`과 같은 규칙).
 */
export function SectionLabel({ text, className }: { text: string; className?: string }) {
  return (
    <p className={`clinic-look-label${className ? ` ${className}` : ''}`} aria-hidden="true">
      {text}
    </p>
  )
}
