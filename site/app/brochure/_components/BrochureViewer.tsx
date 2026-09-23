'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

import { trackEvent } from '@/lib/analytics'

type Props = {
  token: string
  viewer: 'owner' | 'shared'
  /** 게이트를 막 통과했을 때 — 좁은 화면에서는 소개서를 바로 펼친다. */
  autoOpen?: boolean
}

const NARROW = '(max-width: 760px)'

/**
 * 소개서 뷰어.
 *
 * 넓은 화면: 페이지 안에 16:9로 임베드한다. 넘김·키보드·스와이프·타이핑 같은 인터랙션은
 * 효진님 원본 스크립트가 iframe 안에서 그대로 돈다.
 *
 * 좁은 화면: 임베드하면 iframe 안 스크롤과 페이지 스크롤이 겹쳐 손가락이 어느 쪽을
 * 움직이는지 모르게 된다. 그래서 카드 하나만 두고, 누르면 화면 전체를 덮는 오버레이로
 * 연다. 오버레이 안의 문서는 세로 흐름(`rp-flow`)으로 읽힌다.
 */
export default function BrochureViewer({ token, viewer, autoOpen = false }: Props) {
  const [narrow, setNarrow] = useState<boolean | null>(null)
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const frameBox = useRef<HTMLDivElement>(null)
  const src = viewer === 'shared' ? `/brochure/doc?s=${encodeURIComponent(token)}` : '/brochure/doc'

  useEffect(() => {
    const mq = window.matchMedia(NARROW)
    const apply = () => setNarrow(mq.matches)
    apply()
    mq.addEventListener('change', apply)
    return () => mq.removeEventListener('change', apply)
  }, [])

  useEffect(() => {
    if (narrow && autoOpen) setOpen(true)
  }, [narrow, autoOpen])

  // 오버레이가 열려 있는 동안 뒤 페이지가 같이 스크롤되지 않게 한다.
  useEffect(() => {
    if (!open) return
    const previous = document.documentElement.style.overflow
    document.documentElement.style.overflow = 'hidden'
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => {
      document.documentElement.style.overflow = previous
      window.removeEventListener('keydown', onKey)
    }
  }, [open])

  useEffect(() => {
    if (narrow === false || open) trackEvent('brochure_open', { viewer, layout: narrow ? 'flow' : 'deck' })
  }, [narrow, open, viewer])

  const share = useCallback(async () => {
    const url = `${window.location.origin}/brochure/s/${encodeURIComponent(token)}`
    try {
      await navigator.clipboard.writeText(url)
    } catch {
      window.prompt('아래 링크를 복사해 주세요.', url)
    }
    setCopied(true)
    window.setTimeout(() => setCopied(false), 2400)
    trackEvent('brochure_share', { viewer })
    // 공유 자체도 열람 기록에 남긴다 — 병원 안에서 검토가 시작됐다는 신호다.
    try {
      const body = JSON.stringify({ t: token, v: viewer, d: 'parent', s: 'parent', e: [{ k: 'share', p: 0, ms: 0, at: Date.now() }] })
      navigator.sendBeacon?.('/api/brochure/events', new Blob([body], { type: 'text/plain' }))
    } catch {
      // 계측 실패는 공유를 막지 않는다.
    }
  }, [token, viewer])

  const fullscreen = useCallback(() => {
    frameBox.current?.requestFullscreen?.().catch(() => undefined)
  }, [])

  const shareButton = (
    <button type="button" className="brochure-tool" onClick={share}>
      {copied ? '링크를 복사했습니다' : '원장님 공유용 링크 복사'}
    </button>
  )

  if (narrow === null) return <div className="brochure-stage-placeholder" aria-hidden="true" />

  if (narrow) {
    return (
      <div className="brochure-card">
        <button type="button" className="brochure-card-cover" onClick={() => setOpen(true)}>
          <span className="brochure-card-brand">
            Re<span className="brand-colon">:</span>putation
          </span>
          <strong>서비스 소개서</strong>
          <span className="brochure-card-meta">12페이지 · 약 3분</span>
          <span className="brochure-card-open">소개서 펼치기 →</span>
        </button>
        <div className="brochure-tools">{shareButton}</div>

        {open && (
          <div className="brochure-overlay" role="dialog" aria-modal="true" aria-label="Re:putation 서비스 소개서">
            <div className="brochure-overlay-bar">
              <span>서비스 소개서</span>
              <div>
                <button type="button" onClick={share}>
                  {copied ? '복사됨' : '공유'}
                </button>
                <button type="button" onClick={() => setOpen(false)}>
                  닫기
                </button>
              </div>
            </div>
            <iframe className="brochure-overlay-frame" src={src} title="Re:putation 서비스 소개서" />
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="brochure-viewer">
      <div className="brochure-stage" ref={frameBox}>
        <iframe src={src} title="Re:putation 서비스 소개서" allow="fullscreen" />
      </div>
      <div className="brochure-tools">
        <p className="brochure-hint">화면 좌우를 누르거나 ← → 키로 넘길 수 있습니다.</p>
        <button type="button" className="brochure-tool" onClick={fullscreen}>
          전체 화면으로 보기
        </button>
        {shareButton}
      </div>
    </div>
  )
}
