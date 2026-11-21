import { useEffect } from 'react'

/**
 * 저장하지 않은 변경이 있으면 화면을 떠나기 전에 묻는다(결정 대기 3번 A draft).
 *
 * 브라우저 이동·새로고침은 beforeunload가 막는다. 앱 안 링크(Next Link)는 문서를 떠나지 않아
 * beforeunload가 울리지 않으므로 링크 누름을 먼저 가로챈다 — 레이아웃 편집기
 * (`info/layout/useSiteConfigEditor.ts`)와 같은 방식이다. 같은 화면 안 앵커 이동은 묻지 않는다.
 */
// 지금 켜진 이탈 확인 문구. 링크가 아닌 이동(모바일 탭 선택기의 router.push 등)도 이 값으로 묻는다.
let activeMessage: string | null = null

/** 링크가 아닌 코드 이동 직전에 부른다. 지킬 변경이 없거나 사람이 확인하면 true. */
export function confirmLeaveIfGuarded(): boolean {
  return activeMessage === null || window.confirm(activeMessage)
}

export function useLeaveGuard(dirty: boolean, message: string): void {
  useEffect(() => {
    if (!dirty) return
    activeMessage = message
    const unload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
    }
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      const anchor = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
      if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) return
      const next = new URL(anchor.href, window.location.href)
      if (next.origin !== window.location.origin) return
      if (next.pathname === window.location.pathname && next.search === window.location.search) return
      if (!window.confirm(message)) {
        event.preventDefault()
        event.stopPropagation()
      }
    }
    window.addEventListener('beforeunload', unload)
    document.addEventListener('click', click, true)
    return () => {
      if (activeMessage === message) activeMessage = null
      window.removeEventListener('beforeunload', unload)
      document.removeEventListener('click', click, true)
    }
  }, [dirty, message])
}
