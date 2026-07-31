"use client";

import { useEffect, useState } from "react";

import { GeminiLogo, OpenAiLogo } from "./AiLogos";

const PLATFORMS = [
  { name: "ChatGPT", Logo: OpenAiLogo },
  { name: "Gemini", Logo: GeminiLogo },
] as const;

/** 한 칸이 머무는 시간. 읽고 넘어갈 만큼 두되 지루하지 않게. */
const HOLD_MS = 2800;

/**
 * 몇 번 넘긴 뒤 멈출 것인가. 2면 ChatGPT → Gemini → ChatGPT로 한 바퀴 돌고 선다.
 *
 * **끝없이 도는 것이 문제였다.** 제목 한가운데서 로고가 영원히 교대하면, 읽는 사람은
 * 측정 범위가 아니라 '움직이는 장치'를 본다 — 측정 역량보다 AI를 다룬다는 신기함이
 * 앞에 서고, 나머지 문장을 읽는 동안에도 시야 끝이 계속 흔들린다.
 * 한 바퀴 돌고 멈추면 같은 정보를 다 보여주면서 제목이 문장으로 가라앉는다.
 *
 * 다시 무한 회전으로 되돌리려면 이 값을 `Infinity`로 두면 된다.
 */
const TURNS_BEFORE_REST = 2;

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
 *
 * ## 한 바퀴 돌고 선다
 *
 * `TURNS_BEFORE_REST`만큼만 넘기고 멈춘다. 무한히 도는 슬롯은 측정 범위가 아니라
 * 장치를 보게 만든다 — 자세한 이유는 그 상수의 주석에 있다.
 */
export default function RollingAiLogo() {
  const [index, setIndex] = useState(0);

  useEffect(() => {
    if (prefersReducedMotion()) return;

    // 넘긴 횟수는 상태가 아니라 지역 변수로 센다. 상태로 두면 값이 바뀔 때마다
    // effect가 다시 돌아 타이머가 초기화되고, 결국 멈추지 않는다.
    let turns = 0;
    const timer = setInterval(() => {
      setIndex((current) => (current + 1) % PLATFORMS.length);
      turns += 1;
      if (turns >= TURNS_BEFORE_REST) clearInterval(timer);
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
