"use client";

import { useEffect, useState } from "react";

import type { AnswerExample } from "@/lib/landing-copy";

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
 * 아니라 방해다. 한 번 개입한 뒤로는 사용자가 주도한다.
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

  useEffect(() => {
    if (!auto) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const timer = setInterval(() => {
      setActive((current) => (current + 1) % examples.length);
    }, HOLD_MS);
    return () => clearInterval(timer);
  }, [auto, examples.length]);

  return (
    <div className="answer-explorer">
      <div className="answer-tabs" role="tablist" aria-label="진료과별 예시">
        {examples.map((example, index) => (
          <button
            key={example.tag}
            type="button"
            role="tab"
            aria-selected={index === active}
            className={index === active ? "is-active" : ""}
            onClick={() => {
              setAuto(false);
              setActive(index);
            }}
          >
            {example.tag}
          </button>
        ))}
      </div>

      <DiagnosisPreview example={examples[active]} disclaimer={disclaimer} />
    </div>
  );
}
