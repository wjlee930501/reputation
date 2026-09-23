import type { AnswerContent } from "@/lib/landing-copy";

/** 0: 질문만 · 1: 답변과 병원 목록 · 2: 출처와 우리 자리까지 */
export type SceneStep = 0 | 1 | 2;

/**
 * 환자가 AI에게 병원을 묻는 장면.
 *
 * ## 앞 버전이 값싸 보였던 이유 두 가지
 *
 * 1. **맥 윈도우 점 세 개와 말풍선.** 남의 앱 크롬을 흉내낸 목업은 그 자체로 싸구려로
 *    읽힌다. 크롬을 전부 걷어내고 내용만 남긴다 — 환자가 보는 것도 창틀이 아니라 답이다.
 * 2. **병원을 한 곳만 보여줬다.** 그러면 "답변에 들어갈 자리는 서너 곳"이라는 이 서비스의
 *    전제가 화면에서 사라진다. 번호 목록이라야 원장님이 "그 안에 우리가 있나"를 묻는다.
 *
 * ## 단계를 밖에서 받는 이유
 *
 * 이 카드는 두 곳에서 쓰인다 — 스크롤에 묶인 시퀀스(`SceneSequence`)와 자동 순환.
 * 타이머를 안에 두면 두 사용처가 서로의 상태를 덮어쓴다. 여기서는 그리기만 한다.
 */
export default function AiAnswerScene({
  example,
  disclaimer,
  askLine,
  step,
}: {
  example: AnswerContent;
  disclaimer: string;
  askLine: string;
  step: SceneStep;
}) {
  return (
    <div className="scene" data-step={step}>
      {/* 가장 큰 단계(2)를 보이지 않게 같은 칸에 겹쳐 그린다. 카드는 늘 그 높이를 차지하고
          보이는 단계만 그 안에서 바뀐다 — 고정 수치 없이 어느 폭에서도 높이가 같다. */}
      <div className="scene-frame">
        <SceneBody example={example} disclaimer={disclaimer} askLine={askLine} step={step} />
        <div className="scene-ghost" aria-hidden="true" inert>
          <SceneBody example={example} disclaimer={disclaimer} askLine={askLine} step={2} />
        </div>
      </div>
    </div>
  );
}

function SceneBody({
  example,
  disclaimer,
  askLine,
  step,
}: {
  example: AnswerContent;
  disclaimer: string;
  askLine: string;
  step: SceneStep;
}) {
  return (
    <div className="scene-body">
      <div className="scene-ask">
        <span className="scene-eyebrow">환자가 묻습니다</span>
        <p>{example.question}</p>
      </div>

      <div className="scene-answer">
        {step === 0 ? (
          /* 대기 표시는 장식이므로 스크린리더에서 감춘다 — 최종 답변만 읽히면 된다. */
          <p className="scene-thinking" aria-hidden="true">
            <i />
            <i />
            <i />
          </p>
        ) : (
          <>
            <p className="scene-intro">{example.answerIntro}</p>

            <ol className="scene-clinics">
              {example.answerClinics.map((clinic, index) => (
                <li key={clinic} style={{ "--i": index } as React.CSSProperties}>
                  <span className="scene-clinic-name">{clinic}</span>
                  {index === 0 && <span className="scene-clinic-why">{example.answerReason}</span>}
                </li>
              ))}
            </ol>

            {step === 2 && (
              <p className="scene-sources">
                <span>출처</span>
                {example.answerSources.join(" · ")}
              </p>
            )}
          </>
        )}
      </div>

      {/* 이 한 줄이 화면을 주장으로 바꾼다. 목록만 두면 그냥 검색 결과다.
          목록 아래 비어 있는 "넷째 자리"로 그린다 — 지도의 우리 병원 자리와 같은 주황 점선 원. */}
      {step === 2 && (
        <div className="scene-ask-line">
          <span className="scene-ask-slot" aria-hidden="true">
            ?
          </span>
          <p>{askLine}</p>
        </div>
      )}
      <p className="scene-disclaimer">{disclaimer}</p>
    </div>
  );
}
