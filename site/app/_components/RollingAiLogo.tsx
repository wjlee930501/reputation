"use client";

import { useEffect, useRef, useState } from "react";

import { isMotionAllowed, subscribeMotionState } from "@/lib/motion-preference";

import { GeminiLogo, OpenAiLogo } from "./AiLogos";

const PLATFORMS = [
  { name: "ChatGPT", Logo: OpenAiLogo },
  { name: "Gemini", Logo: GeminiLogo },
] as const;

/** 한 칸이 머무는 시간. 읽고 넘어갈 만큼 두되 지루하지 않게. */
const HOLD_MS = 2800;

/** `.rolling-ai-track`의 CSS transition과 같아야 한다. 되감기 시점을 이 값으로 잡는다. */
const SLIDE_MS = 620;

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
 * ## 마지막 칸 뒤에 첫 칸을 한 번 더 둔다
 *
 * 두 칸을 0↔1로 오가면 되돌아올 때 슬롯이 **아래로 미끄러져** 튕기는 것처럼 보인다.
 * 한 방향으로 계속 구르게 하려면 끝에 첫 칸의 사본이 필요하다:
 *
 *     ChatGPT → Gemini → ChatGPT(사본)  ← 여기서 애니메이션 끄고 0번으로 순간이동
 *
 * 사본에 도착한 뒤 transition을 끈 채 0번으로 되감으면 눈에는 계속 같은 방향으로
 * 구르는 것으로 보인다.
 *
 * ## 무한히 구르므로 멈출 수단에 연결한다
 *
 * 5초 넘게 자동으로 움직이는 것에는 멈출 수단이 있어야 한다(WCAG 2.2.2). 이 페이지의
 * 그 수단은 헤더의 "움직임 멈추기" 하나이므로 여기서도 `data-motion`을 구독한다 —
 * 구독하지 않으면 정지 버튼이 멈추지 못하는 유일한 모션이 된다.
 */
export default function RollingAiLogo() {
  /** 0..PLATFORMS.length — 마지막 값은 첫 칸의 사본 자리다. */
  const [index, setIndex] = useState(0);
  /** 되감는 순간에만 false. transition을 끈 채로 순간이동시킨다. */
  const [sliding, setSliding] = useState(true);
  const rewinding = useRef(false);

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | undefined;

    const stop = () => {
      if (timer) clearInterval(timer);
      timer = undefined;
    };

    const start = () => {
      stop();
      timer = setInterval(() => setIndex((current) => current + 1), HOLD_MS);
    };

    const sync = () => {
      if (isMotionAllowed()) start();
      else stop();
    };

    sync();
    const unsubscribe = subscribeMotionState(sync);
    return () => {
      stop();
      unsubscribe();
    };
  }, []);

  // 사본 칸에 도착하면 미끄러짐이 끝난 뒤 0번으로 되감는다.
  useEffect(() => {
    if (index < PLATFORMS.length || rewinding.current) return;
    rewinding.current = true;

    const settle = setTimeout(() => {
      setSliding(false);
      setIndex(0);

      // 같은 프레임에 transition을 되살리면 되감기가 애니메이션된다. 두 프레임 뒤에 켠다.
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          setSliding(true);
          rewinding.current = false;
        }),
      );
    }, SLIDE_MS);

    return () => clearTimeout(settle);
  }, [index]);

  return (
    <span className="rolling-ai">
      {/* 스크린리더·크롤러가 읽는 정본. 굴러가는 칸은 여기서 제외된다. */}
      <span className="sr-only">ChatGPT와 Gemini</span>

      <span className="rolling-ai-window" aria-hidden="true">
        <span
          className="rolling-ai-track"
          data-sliding={sliding ? "on" : "off"}
          // 트랙 높이의 %가 아니라 **한 칸 높이**만큼 민다. 앞 버전은 `translateY(-100%)`가
          // 트랙 전체 높이(칸 수 × 한 칸)를 밀어서, 두 칸이 모두 창 위로 빠져나가
          // 슬롯이 빈 채로 멈춰 있었다.
          style={{ "--rolling-i": index } as React.CSSProperties}
        >
          {[...PLATFORMS, PLATFORMS[0]].map(({ name, Logo }, i) => (
            <span className="rolling-ai-item" key={`${name}-${i}`}>
              <Logo className="rolling-ai-mark" />
              {name}
            </span>
          ))}
        </span>
      </span>
    </span>
  );
}
