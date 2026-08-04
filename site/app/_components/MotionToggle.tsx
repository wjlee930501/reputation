"use client";

import { useEffect, useState } from "react";

import {
  MOTION_PAUSED,
  MOTION_RUNNING,
  prefersReducedMotion,
  readMotionState,
  setMotionState,
  subscribeMotionState,
} from "@/lib/motion-preference";

/**
 * 페이지의 움직임을 멈추는 단 하나의 수단.
 *
 * 헤더 안에 두되 `.header-nav` **밖**에 둔다 — 내비는 600px 이하에서 통째로 숨는데,
 * 좁은 화면에서 멈춤 수단이 사라지면 이 버튼을 만든 이유가 없어진다.
 *
 * OS에서 이미 모션을 줄인 사용자에게는 렌더하지 않는다. 그쪽은 이미 멈춰 있으므로
 * 버튼이 있으면 "멈춰 있는데 멈춤 버튼이 있는" 상태가 된다.
 */
export default function MotionToggle() {
  const [paused, setPaused] = useState(false);
  const [reduced, setReduced] = useState(true); // 판정 전에는 숨긴다(깜빡임 방지)

  useEffect(() => {
    setReduced(prefersReducedMotion());
    setPaused(readMotionState() === MOTION_PAUSED);
    return subscribeMotionState(() => setPaused(readMotionState() === MOTION_PAUSED));
  }, []);

  if (reduced) return null;

  return (
    <button
      type="button"
      className="motion-toggle"
      aria-pressed={paused}
      onClick={() => setMotionState(paused ? MOTION_RUNNING : MOTION_PAUSED)}
    >
      {paused ? "움직임 다시 켜기" : "움직임 멈추기"}
    </button>
  );
}
