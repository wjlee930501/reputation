"use client";

import { useEffect, useState } from "react";

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
 * ## 스크롤과의 연결을 완전히 끊는다
 *
 * 앞 버전은 화면에 35% 들어오면 **0단계부터 시작**하고 나가면 멈췄다. 의도는
 * "방문자가 도착하기 전에 몇 바퀴가 지나가 있지 않게"였는데, 실제로 읽는 사람에게는
 * 스크롤 위치가 내용을 바꾸는 것처럼 보인다 — 천천히 내리면 눈앞에서 시퀀스가
 * 시작되고, 지나갔다 돌아오면 처음부터 다시 시작한다. 시간이 넘기는 판인데
 * 스크롤이 넘기는 판처럼 읽히는 것이다.
 *
 * 이제 마운트되면 그냥 돈다. 틀은 미리 잡혀 있고(아래 고정 영역) 그 안에서 내용만
 * 바뀐다. 스크롤 위치는 이 컴포넌트가 알지 못한다.
 *
 * 탭이 백그라운드로 가면 멈춘다 — 보이지 않는 곳에서 타이머를 돌릴 이유는 없다.
 * 돌아오면 **멈춘 단계에서 이어간다**(0으로 되돌리지 않는다). 되돌리면 스크롤로
 * 되감기던 그 어색함이 탭 전환으로 자리만 옮긴 셈이 된다.
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
  const [step, setStep] = useState<SceneStep>(0);
  // 멈춤 상태가 바뀌면 아래 효과를 다시 돌리기 위한 카운터.
  const [motionEpoch, setMotionEpoch] = useState(0);

  useEffect(() => subscribeMotionState(() => setMotionEpoch((n) => n + 1)), []);

  useEffect(() => {
    if (!isMotionAllowed()) {
      setStep(2);
      return;
    }

    let timer: ReturnType<typeof setTimeout> | undefined;
    // 현재 단계를 ref가 아니라 지역 변수로 들고 간다 — 탭이 돌아왔을 때 이어갈
    // 지점이 필요하고, state는 이 클로저 안에서 최신값이 아니기 때문이다.
    let current: SceneStep = 0;

    // 다음 단계를 예약한다. setInterval을 쓰지 않는 이유는 단계마다 머무는 시간이
    // 다르기 때문이다 — 균등 간격이면 결론이 지나가는 속도가 도입부와 같아진다.
    const advance = (from: SceneStep) => {
      timer = setTimeout(() => {
        const next = ((from + 1) % STEPS.length) as SceneStep;
        current = next;
        setStep(next);
        advance(next);
      }, HOLD_MS[from]);
    };

    const stop = () => {
      if (timer) clearTimeout(timer);
      timer = undefined;
    };

    // 탭이 숨으면 멈추고, 돌아오면 **멈춘 단계에서** 이어간다.
    const onVisibility = () => {
      if (document.hidden) stop();
      else if (!timer) advance(current);
    };

    advance(current);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      stop();
    };
  }, [motionEpoch]);

  return (
    <div className="scene-seq">
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
