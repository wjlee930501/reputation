"use client";

import { useEffect, useRef, useState } from "react";

import type { AnswerContent } from "@/lib/landing-copy";

import { GeminiLogo, OpenAiLogo } from "./AiLogos";

const TYPING_MS = 850;

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * AI 답변 예시 패널. 뷰포트에 처음 들어올 때, 그리고 example이 바뀔 때마다
 * "작성 중(타이핑)" → "답변 등장(스태거 페이드)" 시퀀스를 재생한다.
 * prefers-reduced-motion이면 애니메이션 없이 즉시 답변을 표시한다.
 */
export default function AiAnswerPanel({
  example,
  disclaimer,
}: {
  example: AnswerContent;
  disclaimer: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [seen, setSeen] = useState(false);
  const [phase, setPhase] = useState<"typing" | "answer">("typing");
  const [animKey, setAnimKey] = useState(0);

  // 처음 뷰포트에 들어오면 시퀀스 재생을 허용한다.
  useEffect(() => {
    if (prefersReducedMotion() || !("IntersectionObserver" in window) || !rootRef.current) {
      setSeen(true);
      return;
    }
    const el = rootRef.current;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setSeen(true);
          io.disconnect();
        }
      },
      { threshold: 0.3 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // 보이기 시작했거나 example이 바뀌면 타이핑 → 답변 시퀀스 재생.
  useEffect(() => {
    if (!seen) return;
    setAnimKey((key) => key + 1);
    if (prefersReducedMotion()) {
      setPhase("answer");
      return;
    }
    setPhase("typing");
    const timer = setTimeout(() => setPhase("answer"), TYPING_MS);
    return () => clearTimeout(timer);
  }, [seen, example]);

  return (
    <div className="ai-panel" ref={rootRef} aria-label="AI 답변 예시 화면">
      <span className="ai-panel-watermark" aria-hidden="true">예시</span>
      <div className="ai-panel-head">
        <span className="ai-panel-dot" aria-hidden="true" />
        <span className="ai-panel-dot" aria-hidden="true" />
        <span className="ai-panel-dot" aria-hidden="true" />
        <span className="ai-panel-engines">
          <OpenAiLogo className="ai-panel-engine" />
          <GeminiLogo className="ai-panel-engine" />
          <strong>AI 답변</strong>
        </span>
      </div>

      <div className="ai-turn ai-turn-user">
        <span className="ai-turn-role">환자</span>
        <p>{example.question}</p>
      </div>

      <div className="ai-turn ai-turn-assistant">
        <span className="ai-turn-role">AI</span>
        {phase === "typing" ? (
          <div className="ai-answer ai-answer-typing" aria-hidden="true">
            <span className="ai-typing-dots">
              <i />
              <i />
              <i />
            </span>
            <span className="ai-typing-label">답변을 작성하고 있습니다</span>
          </div>
        ) : (
          <div className="ai-answer" key={animKey}>
            <p className="ai-rise" style={{ "--rise-delay": "0ms" } as React.CSSProperties}>
              {example.answerIntro}
            </p>
            <div
              className="ai-answer-clinic ai-rise"
              style={{ "--rise-delay": "110ms" } as React.CSSProperties}
            >
              <strong>{example.answerClinic}</strong>
              <p>{example.answerReason}</p>
              <ul className="ai-answer-sources">
                {example.answerSources.map((source, index) => (
                  <li
                    key={source}
                    className="ai-rise"
                    style={{ "--rise-delay": `${230 + index * 70}ms` } as React.CSSProperties}
                  >
                    {source}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>

      <p className="ai-panel-disclaimer">{disclaimer}</p>
    </div>
  );
}
