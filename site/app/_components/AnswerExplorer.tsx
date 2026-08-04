"use client";

import { useEffect, useRef, useState } from "react";

import type { AnswerExample } from "@/lib/landing-copy";
import { isMotionAllowed, subscribeMotionState } from "@/lib/motion-preference";

import DiagnosisPreview from "./DiagnosisPreview";

/** 한 진료과가 머무는 시간. 읽고 넘어갈 만큼은 두되 기다리게 하지 않는다. */
const HOLD_MS = 4200;

/**
 * 진료과 탭으로 리포트 예시를 전환하는 탐색기.
 *
 * ## 자동으로 넘어간다
 *
 * 클릭해야만 바뀌면 방문자 대부분은 첫 진료과 하나만 보고 지나간다. 네 가지가 스스로
 * 넘어가야 "우리 진료과도 되나"에 답이 된다.
 *
 * **직접 탭을 누르면 순환을 멈춘다.** 읽으려고 고른 화면이 몇 초 뒤 바뀌면 그건 도움이
 * 아니라 방해다. 한 번 개입한 뒤로는 사용자가 주도한다. 헤더의 "움직임 멈추기"도 같은
 * 결과를 낸다(WCAG 2.2.2).
 *
 * ## 탭은 진짜 탭이어야 한다
 *
 * 앞 버전은 `role="tablist"`/`role="tab"`만 선언하고 `aria-controls`도 `role="tabpanel"`도
 * 없었다. 스크린리더는 "탭 1/4"이라 읽는데 연결된 패널이 없고, 화살표 키도 듣지 않았다.
 * 역할 이름만 빌리고 규약은 지키지 않으면 아무 역할도 없는 것보다 나쁘다 — 사용자가
 * 탭의 조작법을 기대하게 만들어 놓고 배신하기 때문이다.
 */
export default function AnswerExplorer({
  examples,
  disclaimer,
}: {
  examples: AnswerExample[];
  disclaimer: string;
}) {
  const [active, setActive] = useState(0);
  const [auto, setAuto] = useState(true);
  const [motionEpoch, setMotionEpoch] = useState(0);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);

  useEffect(() => subscribeMotionState(() => setMotionEpoch((n) => n + 1)), []);

  useEffect(() => {
    if (!auto) return;
    if (!isMotionAllowed()) return;
    const timer = setInterval(() => {
      setActive((current) => (current + 1) % examples.length);
    }, HOLD_MS);
    return () => clearInterval(timer);
  }, [auto, examples.length, motionEpoch]);

  /** 탭 목록의 표준 키보드 조작 — 좌우 이동, 처음/끝. 선택과 동시에 순환을 멈춘다. */
  function handleKeyDown(event: React.KeyboardEvent<HTMLButtonElement>, index: number) {
    const lastIndex = examples.length - 1;
    const next =
      event.key === "ArrowRight" ? (index === lastIndex ? 0 : index + 1)
      : event.key === "ArrowLeft" ? (index === 0 ? lastIndex : index - 1)
      : event.key === "Home" ? 0
      : event.key === "End" ? lastIndex
      : null;
    if (next === null) return;

    event.preventDefault();
    setAuto(false);
    setActive(next);
    tabRefs.current[next]?.focus();
  }

  return (
    <div className="answer-explorer">
      <div className="answer-tabs" role="tablist" aria-label="진료과별 예시">
        {examples.map((example, index) => {
          const selected = index === active;
          return (
            <button
              key={example.tag}
              type="button"
              role="tab"
              id={`answer-tab-${index}`}
              aria-selected={selected}
              aria-controls={`answer-panel-${index}`}
              // roving tabindex — 탭 목록 전체가 아니라 선택된 탭 하나만 탭 순서에 든다.
              tabIndex={selected ? 0 : -1}
              ref={(node) => {
                tabRefs.current[index] = node;
              }}
              className={selected ? "is-active" : ""}
              onKeyDown={(event) => handleKeyDown(event, index)}
              onClick={() => {
                setAuto(false);
                setActive(index);
              }}
            >
              {example.tag}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`answer-panel-${active}`}
        aria-labelledby={`answer-tab-${active}`}
        tabIndex={0}
      >
        <DiagnosisPreview example={examples[active]} disclaimer={disclaimer} />
      </div>
    </div>
  );
}
