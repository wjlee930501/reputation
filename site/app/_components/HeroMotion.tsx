"use client";

import { useEffect, useRef, useState } from "react";

import { isMotionAllowed, subscribeMotionState } from "@/lib/motion-preference";

/** 이 폭 아래로는 아예 받지 않는다. `.hero-artwork`의 720px 기준과 같은 선이다. */
const MIN_WIDTH = 721;

/**
 * 히어로 라인 아트를 움직이는 판으로 덮는다.
 *
 * ## SVG를 대체하지 않고 **위에 얹는다**
 *
 * `HeroLineArt`(인라인 SVG)는 서버가 그대로 그리므로 JS가 없든 느리든 첫 화면에 항상
 * 있다. 이 컴포넌트는 재생이 실제로 시작된 뒤에만 나타나고, 그때 아래 SVG를 숨긴다
 * (`data-hero-motion` → CSS). 그래서 다음 경우에 **지금과 똑같은 화면**이 남는다:
 *
 *   - JS 실패 · 네트워크에서 mp4가 죽음 · 코덱 미지원
 *   - `prefers-reduced-motion: reduce`
 *   - 헤더의 "움직임 멈추기"를 누른 상태
 *   - 720px 이하 (모바일에는 160KB를 밀어넣지 않는다)
 *
 * ## 네 번째 자율 모션이라는 것을 알고 넣는다
 *
 * 이 페이지에는 이미 질문 띠·장면 시퀀스·리포트 탭이 돈다. 그래서 최소한 **기존 정지
 * 수단 하나로 같이 멈춰야** 한다 — `data-motion`을 구독하는 이유다. 재생 여부를 상태로
 * 두지 않고 매번 `isMotionAllowed()`로 다시 묻는 것도 같은 이유다(OS 설정이 우선).
 */
export default function HeroMotion() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [mounted, setMounted] = useState(false);
  const [playing, setPlaying] = useState(false);

  // 서버 HTML에는 <video>를 넣지 않는다. 넣으면 preload 정책과 무관하게 브라우저가
  // 메타데이터를 받으러 가고, 재생하지 않을 사용자(모바일·감속 설정)도 요청을 낸다.
  useEffect(() => {
    if (window.innerWidth < MIN_WIDTH) return;
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return;
    const video = videoRef.current;
    if (!video) return;

    const sync = () => {
      if (isMotionAllowed()) {
        void video.play().catch(() => setPlaying(false));
      } else {
        video.pause();
        setPlaying(false);
      }
    };

    sync();
    return subscribeMotionState(sync);
  }, [mounted]);

  useEffect(() => {
    if (!playing) return;
    document.documentElement.setAttribute("data-hero-motion", "on");
    return () => document.documentElement.removeAttribute("data-hero-motion");
  }, [playing]);

  if (!mounted) return null;

  return (
    <video
      ref={videoRef}
      className="hero-artwork-video"
      // 배경이므로 접근성 트리에서 뺀다 — 정보를 지고 있지 않다.
      aria-hidden="true"
      muted
      playsInline
      loop
      preload="none"
      // 첫 프레임이 SVG와 같은 그림이라 포스터를 따로 두지 않는다. 아래 SVG가 곧 포스터다.
      onPlaying={() => setPlaying(isMotionAllowed())}
      onPause={() => setPlaying(false)}
      onError={() => setPlaying(false)}
      onStalled={() => setPlaying(false)}
    >
      <source src="/landing/hero-lineart-loop.mp4" type="video/mp4" />
    </video>
  );
}
