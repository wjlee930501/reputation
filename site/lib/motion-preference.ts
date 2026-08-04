/**
 * 페이지 전체의 "움직임 멈춤" 상태.
 *
 * 랜딩에는 스스로 움직이는 것이 셋이다 — 질문 띠(CSS 애니메이션), 장면 시퀀스(타이머),
 * 리포트 탭(타이머). WCAG 2.2.2는 5초 넘게 자동으로 움직이는 것에 멈출 수단을 요구하는데
 * 셋 다 없었다.
 *
 * 컴포넌트마다 버튼을 붙이지 않는 이유:
 * - 질문 띠는 CSS 애니메이션만 쓰는 **서버 컴포넌트**다. 버튼을 달면 클라이언트가 되고
 *   번들이 늘어난다. 상태를 `<html data-motion>`에 두면 CSS가 알아서 멈춘다.
 * - 버튼이 셋이면 "이 페이지를 멈추는 법"이 셋이 된다. 규격도 한 개의 수단이면 된다고 본다.
 *
 * 그래서 진실은 `<html>`의 `data-motion` 하나이고, 타이머를 쓰는 컴포넌트는 이 모듈로
 * 그 값을 구독한다.
 */

export const MOTION_ATTRIBUTE = "data-motion";
export const MOTION_PAUSED = "paused";
export const MOTION_RUNNING = "running";
/** 상태가 바뀔 때 window에 쏘는 이벤트 — React 밖에서 바뀌어도 구독자가 따라온다. */
export const MOTION_EVENT = "reputation:motionchange";

export type MotionState = typeof MOTION_PAUSED | typeof MOTION_RUNNING;

/** OS 설정으로 이미 모션을 줄인 사용자인가. */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * 지금 움직여도 되는가.
 *
 * OS 설정이 우선이다 — 그쪽을 켠 사용자에게는 페이지의 재생 버튼이 있어도 돌리지 않는다.
 */
export function isMotionAllowed(): boolean {
  if (typeof document === "undefined") return false;
  if (prefersReducedMotion()) return false;
  return document.documentElement.getAttribute(MOTION_ATTRIBUTE) !== MOTION_PAUSED;
}

export function readMotionState(): MotionState {
  if (typeof document === "undefined") return MOTION_RUNNING;
  return document.documentElement.getAttribute(MOTION_ATTRIBUTE) === MOTION_PAUSED
    ? MOTION_PAUSED
    : MOTION_RUNNING;
}

export function setMotionState(state: MotionState): void {
  if (typeof document === "undefined") return;
  document.documentElement.setAttribute(MOTION_ATTRIBUTE, state);
  window.dispatchEvent(new CustomEvent(MOTION_EVENT, { detail: state }));
}

/** 상태 변화를 구독한다. 해제 함수를 돌려준다. */
export function subscribeMotionState(listener: () => void): () => void {
  if (typeof window === "undefined") return () => {};
  window.addEventListener(MOTION_EVENT, listener);
  return () => window.removeEventListener(MOTION_EVENT, listener);
}
