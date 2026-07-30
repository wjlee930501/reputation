"use client";

import { useEffect, useRef, useState } from "react";

import { sceneSection, type AnswerExample } from "@/lib/landing-copy";

import AiAnswerScene, { type SceneStep } from "./AiAnswerScene";

const STEPS: { step: SceneStep; caption: string }[] = [
  { step: 0, caption: sceneSection.steps[0] },
  { step: 1, caption: sceneSection.steps[1] },
  { step: 2, caption: sceneSection.steps[2] },
];

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * 스크롤에 묶인 시퀀스 — 카드는 고정되고 설명이 지나간다.
 *
 * 세 구간을 지나며 카드가 단계적으로 열린다: 질문 → 답변과 병원 목록 → 우리 자리.
 * 한 화면에 전부 보여주면 "자리가 서너 곳"이라는 사실이 다른 정보에 묻힌다. 순서대로
 * 드러내야 목록이 나타나는 순간이 보인다.
 *
 * ## 스크롤 위치를 계산하지 않는다
 *
 * `scroll` 이벤트로 진행률을 재면 매 프레임 레이아웃을 읽게 되고 모바일에서 끊긴다.
 * 대신 구간마다 보이지 않는 표식을 두고 **화면 중앙선을 지나는 순간**만 관측한다
 * (rootMargin -50%/-50%). 관측은 브라우저가 알아서 배칭한다.
 *
 * ## 좁은 화면과 모션 최소화
 *
 * 둘 다 고정을 쓰지 않고 마지막 단계를 그대로 보인다. 좁은 화면에서 고정 시퀀스는
 * 스크롤을 빼앗기는 느낌을 주고, 모션을 줄인 사용자에게는 단계 자체가 방해다.
 */
export default function SceneSequence({
  example,
  disclaimer,
}: {
  example: AnswerExample;
  disclaimer: string;
}) {
  const markers = useRef<(HTMLLIElement | null)[]>([]);
  const [step, setStep] = useState<SceneStep>(0);
  const [sequenced, setSequenced] = useState(false);

  useEffect(() => {
    const narrow = window.matchMedia("(max-width: 900px)").matches;
    if (narrow || prefersReducedMotion() || !("IntersectionObserver" in window)) {
      setStep(2);
      return;
    }
    setSequenced(true);

    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const index = markers.current.indexOf(entry.target as HTMLLIElement);
          if (index >= 0) setStep(STEPS[index].step);
        }
      },
      { rootMargin: "-50% 0px -50% 0px", threshold: 0 },
    );
    markers.current.forEach((element) => element && observer.observe(element));
    return () => observer.disconnect();
  }, []);

  return (
    <div className={sequenced ? "scene-seq is-sequenced" : "scene-seq"}>
      <div className="scene-seq-stage">
        <AiAnswerScene
          example={example}
          disclaimer={disclaimer}
          askLine={sceneSection.askLine}
          step={step}
        />
      </div>

      <ol className="scene-seq-steps">
        {STEPS.map((entry, index) => (
          <li
            key={entry.caption}
            ref={(element) => {
              markers.current[index] = element;
            }}
            className={step === entry.step ? "is-active" : ""}
          >
            <span>{entry.caption}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
