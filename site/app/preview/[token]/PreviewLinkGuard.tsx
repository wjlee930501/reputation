'use client'

import { useEffect, useState } from 'react'

const MESSAGE = '미리보기에서는 다른 페이지로 이동하지 않습니다. 공개 화면은 공개 반영 뒤 병원 주소에서 확인해 주세요.'

/**
 * 미리보기 안의 링크 이동을 막는다(같은 화면 안 `#` 이동만 허용).
 *
 * 어드민 편집 화면이 이 화면을 iframe으로 띄운다. 링크를 누르면 iframe이 공개 병원 사이트로 넘어가는데,
 * 그 사이트는 iframe을 막아(`frame-ancestors 'none'`) 빈 화면이 되고, 어드민은 그 이동을 알 수 없다.
 * 전화(`tel:`)·외부 링크도 미리보기에서는 열지 않는다.
 */
export function PreviewLinkGuard() {
  const [notice, setNotice] = useState(false)

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    function onClick(event: MouseEvent) {
      const target = event.target instanceof Element ? event.target.closest('a[href]') : null
      if (!target) return
      const href = target.getAttribute('href') ?? ''
      if (href.startsWith('#')) return
      event.preventDefault()
      event.stopPropagation()
      setNotice(true)
      clearTimeout(timer)
      timer = setTimeout(() => setNotice(false), 3000)
    }
    document.addEventListener('click', onClick, true)
    return () => {
      document.removeEventListener('click', onClick, true)
      clearTimeout(timer)
    }
  }, [])

  if (!notice) return null
  return (
    <p role="status" style={NOTICE_STYLE}>
      {MESSAGE}
    </p>
  )
}

const NOTICE_STYLE = {
  position: 'fixed',
  left: '50%',
  bottom: 24,
  transform: 'translateX(-50%)',
  zIndex: 1001,
  maxWidth: 'calc(100% - 32px)',
  margin: 0,
  padding: '10px 16px',
  borderRadius: 8,
  background: '#1f2937',
  color: '#ffffff',
  fontSize: 14,
  textAlign: 'center',
} as const
