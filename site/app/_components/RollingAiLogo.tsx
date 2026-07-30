"use client";

import { useEffect, useState } from "react";

import { GeminiLogo, OpenAiLogo } from "./AiLogos";

const PLATFORMS = [
  { name: "ChatGPT", Logo: OpenAiLogo },
  { name: "Gemini", Logo: GeminiLogo },
] as const;

/** 한 칸이 머무는 시간. 읽고 넘어갈 만큼 두되 지루하지 않게. */
const HOLD_MS = 2200;

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/**
 * 제목 안에서 AI 서비스 이름이 굴러가는 슬롯.
 *
 * ## 접근성과 SEO를 문장 하나로 해결한다
 *
 * 굴러가는 칸을 그대로 두면 스크린리더가 "ChatGPT Gemini"를 이어 읽어 문장이 깨지고,
 * 크롤러도 같은 것을 본다. 그래서 **읽히는 텍스트와 보이는 애니메이션을 분리**한다:
 * 시각 요소는 `aria-hidden`, 의미는 `.sr-only`에 완성된 문구로 한 번만 둔다.
 *
 * ## JS가 없으면 첫 칸이 그대로 남는다
 *
 * 초기 상태가 0번 칸이고 회전은 `useEffect`에서만 일어나므로, JS가 죽으면 "ChatGPT"가
 * 정상적으로 보인다. 빈 칸이 남지 않는다.
 */
export default function RollingAiLogo() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (prefersReducedMotion()) return;
    const timer = setInterval(() => {
      setIndex((current) => (current + 1) % PLATFORMS.length);
    }, HOLD_MS);
    return () => clearInterval(timer);
  }, []);

  return (
    <span className="rolling-ai">
      {/* 스크린리더·크롤러가 읽는 정본. 굴러가는 칸은 여기서 제외된다. */}
      <span className="sr-only">ChatGPT와 Gemini</span>

      <span className="rolling-ai-window" aria-hidden="true">
        <span
          className="rolling-ai-track"
          style={{ transform: `translateY(-${index * 100}%)` }}
        >
          {PLATFORMS.map(({ name, Logo }) => (
            <span className="rolling-ai-item" key={name}>
              <Logo className="rolling-ai-mark" />
              {name}
            </span>
          ))}
        </span>
      </span>
    </span>
  );
}
