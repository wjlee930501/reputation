import { splitAccent } from "@/lib/landing-accent";

/**
 * 제목 안의 한 구간만 accent로 칠한다(`lib/landing-accent.ts`).
 *
 * `<em>`을 쓰는 이유: 스크린리더와 검색이 읽는 문장은 원문 그대로이고, 칠한 구간은
 * 문장 안의 강조로 남는다. 구간을 못 찾으면 원문을 그대로 그린다.
 */
export default function Accent({ text, accent }: { text: string; accent?: string }) {
  const parts = splitAccent(text, accent);
  if (!parts) return <>{text}</>;

  return (
    <>
      {parts.before}
      <em className="accent">{parts.accent}</em>
      {parts.after}
    </>
  );
}
