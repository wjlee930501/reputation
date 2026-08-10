import {
  MEASUREMENT_TRIALS,
  measurementPlatforms,
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
 * 그래서 히어로 시각물을 리포트의 실제 형태로 바꿨다. 분모 9는 규약(질의 3개 × 반복 3회)에서
 * 오는 실제 값이고, 이 화면이 곧 무료 진단이 보내주는 것의 축소판이다.
 *
 * ## 애니메이션을 JS로 하지 않는 이유
 *
 * 처음에는 눈금을 `useState`로 하나씩 채웠다. 그런데 그러면 **서버가 뱉는 HTML이
 * `0 / 9회 등장`이 된다** — JS가 늦거나 실패하면 히어로에 틀린 숫자가 그대로 남고,
 * 크롤러도 그 값을 읽는다.
 *
 * 그래서 숫자와 채워진 눈금은 항상 최종 상태로 렌더하고, 등장 연출만 CSS
 * `animation-delay`로 준다. 상태가 없으므로 서버 컴포넌트이며(번들 0), 어떤 실패
 * 경로에서도 화면의 숫자가 사실과 어긋나지 않는다.
 */
export default function DiagnosisPreview({
  example,
  disclaimer,
}: {
  example: AnswerContent;
  disclaimer: string;
}) {
  /**
   * 행 라벨은 **실제 발송되는 리포트와 같아야 한다**(퍼널 PRD F5-1).
   *
   * 앞 버전은 `ChatGPT` · `Gemini`였다. 그런데 리포트는 `OpenAI API` · `Google Gemini API`로
   * 나간다 — 우리가 부르는 것은 API이고, 환자가 앱에서 보는 화면과 같다는 주장은 §2-2에서
   * 철회했기 때문이다. "리포트 미리보기"라고 적어 놓고 실물과 다른 라벨을 보여주면,
   * 이 페이지가 파는 측정 규율이 첫 화면에서부터 깨진다.
   */
  const logos = { chatgpt: OpenAiLogo, gemini: GeminiLogo } as const;
  const rows = measurementPlatforms.map((platform) => ({
    ...platform,
    Logo: logos[platform.key],
    hits: example.counts[platform.key],
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
        {rows.map(({ key, label, note, Logo, hits }, rowIndex) => (
          <div className="dx-row" key={key}>
            <span className="dx-platform">
              <Logo className="dx-logo" />
              <span className="dx-platform-name">
                {label}
                <em>{note}</em>
              </span>
            </span>

            {/* 눈금은 옆 숫자의 시각적 표현이므로 스크린리더에서 감춘다. */}
            <span className="dx-scale" aria-hidden="true">
              {Array.from({ length: MEASUREMENT_TRIALS }, (_, index) => (
                <i
                  key={index}
                  className={index < hits ? "is-hit" : ""}
                  // 행마다 조금 늦게 시작해 두 줄이 순서대로 채워지는 것처럼 보이게 한다.
                  style={{ "--i": rowIndex * MEASUREMENT_TRIALS + index } as React.CSSProperties}
                />
              ))}
            </span>

            <span className="dx-count">
              <strong>{hits}</strong>
              <span>/ {MEASUREMENT_TRIALS}회 등장</span>
            </span>
          </div>
        ))}
      </div>

      <div className="dx-cite">
        <span className="dx-eyebrow">검증 기록</span>
        <p>
          질문 원문 · 확인 시각 · 병원명 확인 기준을 함께 공개합니다.
        </p>
      </div>

      <p className="dx-disclaimer">{disclaimer}</p>
    </div>
  );
}
