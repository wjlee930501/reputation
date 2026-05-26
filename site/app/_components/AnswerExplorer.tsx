"use client";

import { useState } from "react";

import type { AnswerExample } from "@/lib/landing-copy";

import AiAnswerPanel from "./AiAnswerPanel";

/**
 * 진료과 탭으로 AI 답변 예시를 전환하는 탐색기.
 * 탭을 바꾸면 AiAnswerPanel이 타이핑 → 답변 시퀀스를 다시 재생한다.
 */
export default function AnswerExplorer({
  examples,
  disclaimer,
}: {
  examples: AnswerExample[];
  disclaimer: string;
}) {
  const [active, setActive] = useState(0);
  const current = examples[active];

  return (
    <div className="answer-explorer">
      <div className="answer-tabs" role="tablist" aria-label="진료과별 AI 답변 예시">
        {examples.map((example, index) => (
          <button
            key={example.tag}
            type="button"
            role="tab"
            aria-selected={index === active}
            className={index === active ? "is-active" : ""}
            onClick={() => setActive(index)}
          >
            {example.tag}
          </button>
        ))}
      </div>

      <AiAnswerPanel example={current} disclaimer={disclaimer} />
    </div>
  );
}
