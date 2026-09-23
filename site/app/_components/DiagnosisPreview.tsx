import {
  exposureGradeIndex,
  exposureGrades,
  measurementPlatforms,
  previewSection,
  type AnswerContent,
} from "@/lib/landing-copy";

import { GeminiLogo, OpenAiLogo } from "./AiLogos";

/**
 * 진단 리포트 미리보기.
 *
 * ## 왜 채팅 창 목업을 버렸나
 *
 * 이전 버전은 ChatGPT 창을 흉내낸 목업이었다 — 맥 윈도우 점 세 개, 말풍선, 태그 알약.
 * 두 가지가 잘못됐다. 첫째, **남의 UI를 모사한 화면은 값싸 보인다.** 둘째, 그건 우리가
 * 파는 것이 아니다. 우리가 주는 것은 답변이 아니라 **그 답변에 몇 번 등장했는지**다.
 *
 * ## 횟수 대신 등급 (2026-09)
 *
 * "4 / 9회 등장"은 원장님께 의미가 없다는 대표 판단에 따라, 플랫폼마다 다섯 등급
 * (`exposureGrades`) 중 하나와 다섯 칸 게이지로만 보여준다. 횟수는 등급을 매기는 재료로만
 * 쓰고 화면에 올리지 않는다.
 *
 * ## 애니메이션을 JS로 하지 않는 이유
 *
 * 처음에는 눈금을 `useState`로 하나씩 채웠다. 그런데 그러면 **서버가 뱉는 HTML이
 * 빈 눈금이 된다** — JS가 늦거나 실패하면 히어로에 틀린 숫자가 그대로 남고,
 * 크롤러도 그 값을 읽는다.
 *
 * 그래서 등급과 채워진 눈금은 항상 최종 상태로 렌더하고, 등장 연출만 CSS
 * `animation-delay`로 준다. 상태가 없으므로 서버 컴포넌트이며(번들 0), 어떤 실패
 * 경로에서도 화면의 등급이 사실과 어긋나지 않는다.
 */
export default function DiagnosisPreview({
  example,
  disclaimer,
}: {
  example: AnswerContent;
  disclaimer: string;
}) {
  /**
   * 행 라벨은 원장님이 아는 이름(ChatGPT · Gemini)으로 둔다. 실제 호출 경로(OpenAI API ·
   * Google Gemini API)는 FAQ "측정은 어떻게 하나요?"에서 밝힌다.
   *
   * 주의: 퍼널 PRD F5-1은 "미리보기 = 실제 리포트"를 요구한다. 등급 표기로 바꾼 지금,
   * 백엔드 리포트도 같은 다섯 등급을 쓰도록 맞춰야 이 원칙이 다시 성립한다.
   */
  const logos = { chatgpt: OpenAiLogo, gemini: GeminiLogo } as const;
  const rows = measurementPlatforms.map((platform) => ({
    ...platform,
    Logo: logos[platform.key],
    grade: exposureGradeIndex(example.counts[platform.key]),
  }));

  return (
    <div className="dx-card">
      <div className="dx-head">
        <span className="dx-title">진단 리포트 미리보기</span>
        <span className="dx-badge">예시</span>
      </div>

      <div className="dx-question">
        <span className="dx-eyebrow">환자 질문</span>
        <p>{example.question}</p>
      </div>

      <div className="dx-results">
        {rows.map(({ key, label, Logo, grade }, rowIndex) => (
          <div className="dx-row" key={key}>
            <span className="dx-platform">
              <Logo className="dx-logo" />
              <span className="dx-platform-name">{label}</span>
            </span>

            {/* 다섯 칸 게이지 — 등급까지 채운다. 값은 옆 글자가 말하므로 스크린리더에서 감춘다. */}
            <span className="dx-scale dx-grade-scale" aria-hidden="true">
              {exposureGrades.map((g, index) => (
                <i
                  key={g.label}
                  className={index <= grade ? "is-hit" : ""}
                  style={{ "--i": rowIndex * exposureGrades.length + index } as React.CSSProperties}
                />
              ))}
            </span>

            <span className="dx-count dx-grade" data-grade={grade}>
              <span className="dx-grade-label">{previewSection.gradeLabel}</span>
              <strong>{exposureGrades[grade].label}</strong>
            </span>
          </div>
        ))}
      </div>

      <div className="dx-cite">
        <span className="dx-eyebrow">{previewSection.explainLabel}</span>
        <p>{previewSection.explain}</p>
      </div>

      <p className="dx-disclaimer">{disclaimer}</p>
    </div>
  );
}
