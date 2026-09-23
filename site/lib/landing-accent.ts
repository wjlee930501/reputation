import {
  ctaSection,
  limitsSection,
  localSection,
  operationSection,
  painSection,
  pricingSection,
  previewSection,
  sceneSection,
} from "./landing-copy.ts";

/**
 * 제목 한 줄 안에서 **색을 입힐 구간**.
 *
 * 제목을 검정 한 톤으로만 두면 긴 문장(이 페이지 제목은 대부분 두 줄이다)에서 눈이
 * 앉을 자리가 없다. 한 구간만 accent로 칠해 "이 줄에서 읽을 것은 여기"를 먼저 보여준다.
 * 구간은 한 제목에 하나만 둔다 — 둘 이상 칠하면 다시 아무 데도 앉지 못한다.
 *
 * 카피 원문은 `landing-copy.ts`가 그대로 쥐고 있고, 여기는 **어느 부분을 칠할지만**
 * 적는다. 카피 파일에 마크업을 섞으면 JSON-LD·llms.txt·테스트가 전부 태그를 벗겨야 한다.
 *
 * 히어로 제목은 여기 없다 — 히어로의 오렌지는 원과 버튼 둘로 끝낸다.
 *
 * 구간은 반드시 원문의 부분 문자열이어야 한다(테스트가 지킨다). 카피가 바뀌어
 * 구간이 원문에서 사라지면 `splitAccent`는 칠하지 않고 원문을 그대로 돌려준다 —
 * 제목이 깨지거나 사라지는 일은 없다.
 */
export const headingAccents: ReadonlyArray<{ text: string; accent: string }> = [
  { text: sceneSection.heading, accent: "우리 병원을 추천하고 있을까요?" },
  { text: painSection.heading, accent: "잘 준비되어 있을까요?" },
  { text: painSection.punchline, accent: painSection.punchlineAccent },
  { text: localSection.heading, accent: "선점 효과는 분명히 존재합니다." },
  { text: previewSection.heading, accent: "노출이 부족한지" },
  { text: operationSection.heading, accent: "모션랩스가 함께합니다." },
  { text: limitsSection.heading, accent: "반드시 지키겠습니다." },
  { text: pricingSection.heading, accent: "합리적으로 선택하세요." },
  { text: ctaSection.heading, accent: "도입문의로" },
];

export type AccentParts = { before: string; accent: string; after: string } | null;

/** 원문을 칠할 구간 앞·구간·뒤로 나눈다. 구간이 없거나 원문에 없으면 `null`. */
export function splitAccent(text: string, accent?: string): AccentParts {
  const phrase = accent ?? headingAccents.find((entry) => entry.text === text)?.accent;
  if (!phrase) return null;
  const at = text.indexOf(phrase);
  if (at < 0) return null;
  return {
    before: text.slice(0, at),
    accent: phrase,
    after: text.slice(at + phrase.length),
  };
}
