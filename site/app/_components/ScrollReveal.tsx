"use client";

import { useEffect } from "react";

/**
 * [data-reveal] 요소를 뷰포트에 들어올 때 페이드/슬라이드 인 시킨다.
 * - JS가 동작할 때만 .reveal-ready를 붙여 초기 숨김을 적용한다(노 JS면 항상 보임).
 * - prefers-reduced-motion이면 즉시 모두 표시하고 옵저버를 만들지 않는다.
 */
export default function ScrollReveal() {
  useEffect(() => {
    const root = document.documentElement;
    const targets = Array.from(document.querySelectorAll<HTMLElement>("[data-reveal]"));
    if (targets.length === 0) return;

    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || !("IntersectionObserver" in window)) {
      targets.forEach((el) => el.classList.add("in-view"));
      return;
    }

    root.classList.add("reveal-ready");
    const observer = new IntersectionObserver(
      (entries, obs) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("in-view");
            obs.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -10% 0px", threshold: 0.12 },
    );

    targets.forEach((el) => observer.observe(el));
    return () => {
      observer.disconnect();
      root.classList.remove("reveal-ready");
    };
  }, []);

  return null;
}
