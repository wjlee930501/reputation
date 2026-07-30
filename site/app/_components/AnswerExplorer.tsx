"use client";

import { useState } from "react";

import type { AnswerExample } from "@/lib/landing-copy";

import DiagnosisPreview from "./DiagnosisPreview";

/**
 * 진료과 탭으로 진단 결과 예시를 전환하는 탐색기.
 * 탭을 바꾸면 DiagnosisPreview가 눈금을 다시 채운다 — 진료과마다 값이 다르다는 사실이
 * 그 움직임으로 드러난다.
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

      <DiagnosisPreview example={current} disclaimer={disclaimer} />
    </div>
  );
}
