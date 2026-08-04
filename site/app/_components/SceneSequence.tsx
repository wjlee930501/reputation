"use client";

import { useEffect, useRef, useState } from "react";

import { sceneSection, type AnswerExample } from "@/lib/landing-copy";

import AiAnswerScene, { type SceneStep } from "./AiAnswerScene";

/**
 * 단계별 머무는 시간.
 *
 * 마지막 단계가 가장 길다 — 목록에 병원 셋이 적히고 그 아래 "여기 이름이 있어야
 * 한다"가 나오는 상태가 이 섹션이 하려는 말이다. 앞의 둘은 거기까지 가는 길이다.
 */
const HOLD_MS: Record<SceneStep, number> = { 0: 1500, 1: 2600, 2: 5200 };

const STEPS: { step: SceneStep; caption: string }[] = [
  { step: 0, caption: sceneSection.steps[0] },
  { step: 1, caption: sceneSection.steps[1] },
  { step: 2, caption: sceneSection.steps[2] },
];

import { isMotionAllowed, subscribeMotionState } from "@/lib/motion-preference";

/**
 * 환자가 묻고 AI가 답하는 장면 — **스스로 넘어간다.**
 *
 * ## 스크롤에 묶지 않는다
 *
 * 앞 버전은 145vh짜리 통을 두고 스크롤 위치로 단계를 바꾸는 고정 시퀀스였다.
 * 그 방식은 **화면 높이마다 다르게 어긋난다** — 카드는 제 높이(약 460px)인데 통은
 * 화면 높이에 비례하니, 큰 화면일수록 제목과 카드 사이 또는 카드 아래가 크게 빈다.
 * 실제로 그 간격을 두 번 고쳤고 두 번 다 다른 크기에서 다시 깨졌다.
 *
 * 지금은 섹션이 제 내용만큼만 높고, 단계는 시간이 넘긴다. 어느 화면에서도 같은
 * 모양이고 고칠 변수가 하나 줄었다.
 *
 * ## 보일 때만 돈다
 *
 * 화면에 들어오면 시작하고 나가면 멈춘다. 그러지 않으면 방문자가 도착하기 전에
 * 몇 바퀴가 지나가 있고, 보이지도 않는 곳에서 타이머가 계속 돈다.
 *
 * ## 모션을 줄인 사용자 · 멈춤을 누른 사용자
 *
 * 마지막 단계를 그대로 보여주고 타이머를 걸지 않는다. 이 섹션의 결론이 3단계이므로
 * 정보 손실이 없다. 헤더의 "움직임 멈추기"도 같은 경로를 탄다 — 상태가 바뀌면 이
 * 효과가 다시 돌면서 타이머를 걷어내고 결론 단계로 고정한다.
 */
export default function SceneSequence({
  example,
  disclaimer,
}: {
  example: AnswerExample;
  disclaimer: string;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [step, setStep] = useState<SceneStep>(0);
  // 멈춤 상태가 바뀌면 아래 효과를 다시 돌리기 위한 카운터.
  const [motionEpoch, setMotionEpoch] = useState(0);

  useEffect(() => subscribeMotionState(() => setMotionEpoch((n) => n + 1)), []);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    if (!isMotionAllowed()) {
      setStep(2);
      return;
    }

    let timer: ReturnType<typeof setTimeout> | undefined;

    // 다음 단계를 예약한다. setInterval을 쓰지 않는 이유는 단계마다 머무는 시간이
    // 다르기 때문이다 — 균등 간격이면 결론이 지나가는 속도가 도입부와 같아진다.
    const advance = (from: SceneStep) => {
      timer = setTimeout(() => {
        const next = ((from + 1) % STEPS.length) as SceneStep;
        setStep(next);
        advance(next);
      }, HOLD_MS[from]);
    };

    const stop = () => {
      if (timer) clearTimeout(timer);
      timer = undefined;
    };

    if (!("IntersectionObserver" in window)) {
      setStep(2);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          if (!timer) advance(0);
        } else {
          stop();
        }
      },
      { threshold: 0.35 },
    );
    observer.observe(host);

    return () => {
      observer.disconnect();
      stop();
    };
  }, [motionEpoch]);

  return (
    <div className="scene-seq" ref={hostRef}>
      <div className="scene-seq-panel">
        <div className="scene-seq-stage">
          <AiAnswerScene
            example={example}
            disclaimer={disclaimer}
            askLine={sceneSection.askLine}
            step={step}
          />
        </div>

        <ol className="scene-seq-steps">
          {STEPS.map((entry) => (
            <li
              key={entry.caption}
              className={step === entry.step ? "is-active" : ""}
              aria-current={step === entry.step ? "step" : undefined}
            >
              <span>{entry.caption}</span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
