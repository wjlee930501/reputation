"use client";

import { useEffect } from "react";

/** 이 거리를 넘어가면 "읽는 중"으로 본다. 짧게 두면 스크롤 한 번에 헤더가 깜빡인다. */
const CONDENSE_AT = 48;

/**
 * 스크롤하면 헤더가 조여든다.
 *
 * 고정 헤더가 처음 높이 그대로 따라다니면 화면을 계속 갉아먹고, 무엇보다 페이지가
 * 아무 반응도 하지 않는 것처럼 보인다. 조금 내려가면 얇아지고 배경이 짙어지게 해서
 * "지금 읽는 중"이라는 상태를 만든다.
 *
 * ## DOM 클래스가 아니라 data 속성을 쓴다
 *
 * 헤더의 `className`은 서버가 그린다. 여기서 클래스 목록을 건드리면 하이드레이션
 * 경고가 나기 쉽다. `data-scrolled`는 서버 마크업에 없던 속성이라 충돌하지 않는다.
 *
 * ## 프레임마다 레이아웃을 읽지 않는다
 *
 * `scroll`에서 `scrollY`만 읽고 rAF로 한 번만 반영한다. 임계값을 넘나들 때만 속성을
 * 바꾸므로 대부분의 프레임에서는 아무 일도 하지 않는다.
 */
export default function HeaderScrollState() {
  useEffect(() => {
    const header = document.querySelector<HTMLElement>(".site-header");
    if (!header) return;

    let ticking = false;
    let condensed: boolean | null = null;

    const apply = () => {
      ticking = false;
      const next = window.scrollY > CONDENSE_AT;
      if (next === condensed) return;
      condensed = next;
      if (next) header.setAttribute("data-scrolled", "");
      else header.removeAttribute("data-scrolled");
    };

    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(apply);
    };

    apply();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      header.removeAttribute("data-scrolled");
    };
  }, []);

  return null;
}
