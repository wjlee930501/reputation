'use client'

import { useCallback, useEffect, useState } from 'react'

type Status = {
  phase: 'MEASURING' | 'BUILDING_REPORT' | 'READY' | 'FAILED' | 'EXPIRED'
  hospital_name: string
  message: string
  report_ready: boolean
}

// 측정은 보통 몇 분 안에 끝난다(luna p90 35초 × 질의 3 × 플랫폼 2 ÷ 동시성).
// 5초 폴링이면 사용자가 화면을 보고 있어도 답답하지 않고, 서버 부담도 없다.
const POLL_MS = 5000
const IN_PROGRESS = new Set(['MEASURING', 'BUILDING_REPORT'])

export default function StatusView({ token }: { token: string }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [notFound, setNotFound] = useState(false)

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/diagnosis/${encodeURIComponent(token)}/status`, {
        cache: 'no-store',
      })
      if (response.status === 404) {
        setNotFound(true)
        return true
      }
      if (!response.ok) return false
      const data = (await response.json()) as Status
      setStatus(data)
      return !IN_PROGRESS.has(data.phase)
    } catch {
      return false
    }
  }, [token])

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false

    const tick = async () => {
      const finished = await load()
      if (cancelled || finished) return
      timer = setTimeout(tick, POLL_MS)
    }
    void tick()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [load])

  if (notFound) {
    return (
      <div className="dg-panel dg-done">
        <h2>유효하지 않은 링크입니다</h2>
        <p className="dg-muted">
          링크가 만료되었거나 주소가 정확하지 않습니다. 신청하신 이메일을 다시 확인해 주세요.
        </p>
      </div>
    )
  }

  if (!status) {
    return (
      <div className="dg-panel dg-done">
        <h2>진행 상황을 불러오는 중…</h2>
      </div>
    )
  }

  return (
    <div className="dg-panel dg-done">
      <p className="dg-eyebrow">{status.hospital_name}</p>
      <h2>
        {status.phase === 'READY'
          ? 'AI 노출 진단 리포트가 준비되었습니다'
          : 'AI 노출 진단 진행 상황'}
      </h2>
      <p aria-live="polite">{status.message}</p>

      {status.report_ready && (
        <p style={{ marginTop: 18 }}>
          <a
            className="dg-submit"
            style={{ display: 'inline-block', width: 'auto', padding: '13px 24px', textDecoration: 'none' }}
            href={`/api/diagnosis/${encodeURIComponent(token)}/report`}
          >
            리포트 열기
          </a>
        </p>
      )}

      {IN_PROGRESS.has(status.phase) && (
        <p className="dg-muted">
          이 페이지는 자동으로 갱신됩니다. 창을 닫으셔도 완료되면 이메일로 알려드립니다.
        </p>
      )}
    </div>
  )
}
