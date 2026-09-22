/**
 * 브라우저에서 광고 캡처값을 읽고 쓰는 얇은 층. 규칙 자체는 `ad-attribution.ts`에 있다.
 *
 * 캡처 시점이 두 곳(레이아웃의 캡처 컴포넌트, 폼)인데 React 이펙트 순서는 자식이 먼저다.
 * 즉 레이아웃이 쓰기 전에 폼이 읽을 수 있다. 그래서 **호출 순서에 의존하지 않도록**
 * `ensureAttributionCaptured()`를 멱등하게 만들고 양쪽 모두 이 함수를 먼저 부른다.
 */

import {
  buildAttributionCookie,
  parseAttribution,
  readAttributionCookie,
  type Attribution,
} from './ad-attribution.ts'

/**
 * 현재 URL에 광고 파라미터가 있으면 캡처해 저장하고, 없으면 저장돼 있던 값을 돌려준다.
 *
 * 새 광고 클릭은 **덮어쓴다**. 지난달 캠페인이 이번 캠페인의 리드를 가로채면 안 된다.
 */
export function ensureAttributionCaptured(): Attribution | null {
  if (typeof window === 'undefined' || typeof document === 'undefined') return null

  let fromUrl: Attribution | null = null
  try {
    fromUrl = parseAttribution(window.location.search, window.location.pathname)
  } catch {
    fromUrl = null
  }

  if (fromUrl) {
    try {
      document.cookie = buildAttributionCookie(fromUrl, window.location.hostname)
    } catch {
      // 쿠키가 막힌 브라우저에서도 이번 세션의 제출에는 값을 쓸 수 있게 그대로 반환한다.
    }
    return fromUrl
  }

  try {
    return readAttributionCookie(document.cookie)
  } catch {
    return null
  }
}
