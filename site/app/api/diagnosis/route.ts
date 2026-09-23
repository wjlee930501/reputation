import { NextResponse } from 'next/server'

import { DIAGNOSIS_RETIRED_MESSAGE } from '@/lib/diagnosis-retired'

export const runtime = 'nodejs'

/**
 * 무료 진단 셀프 신청 — 종료 (2026-09).
 *
 * 예전에는 여기서 신청을 받아 백엔드가 18회 측정 후 리포트를 이메일로 자동 발송했다.
 * 리포트만 받고 연락이 끊기는 경우가 많아, 진단 리포트는 **도입문의를 받은 뒤 담당 마케터가
 * 만들어 연락과 함께 전달**하는 구조로 바꿨다. 신청 화면(`/ai-diagnosis`)은 `/contact`로
 * 보내고(next.config.mjs), 이 접수 API도 닫아 사이트를 거쳐 자동 발송이 시작되지 않게 한다.
 *
 * 이미 신청한 병원의 결과 확인 경로(`/ai-diagnosis/status/[token]`, `/api/diagnosis/[token]/*`)는
 * 그대로 둔다 — 그분들이 받은 메일 속 링크가 살아 있어야 한다.
 */
export async function POST() {
  return NextResponse.json(
    { ok: false, error: DIAGNOSIS_RETIRED_MESSAGE, contactUrl: '/contact' },
    { status: 410, headers: { 'Cache-Control': 'no-store' } },
  )
}
