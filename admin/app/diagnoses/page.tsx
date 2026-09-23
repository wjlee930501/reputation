'use client'

import { useState } from 'react'
import Link from 'next/link'
import { ApiError, fetchAPI } from '@/lib/api'
import { ManualDiagnosisForm } from '@/app/_components/ManualDiagnosisForm'
import { DiagnosisHistory } from './DiagnosisHistory'
import { safeOperatorError } from '@/lib/operations-journey'
import { manualDiagnosisRefusal } from '@/lib/manual-diagnosis'

type Created = {
  readonly leadId: string
  readonly diagnosisId: string
}

/**
 * 노출 진단 생성 — 문의를 기다리지 않고 콜용 보고서를 만든다.
 *
 * 도입문의로 들어온 병원의 값을 **고치는** 경로는 여기가 아니라 상담 요청 화면이다.
 * 그쪽에는 고칠 리드가 이미 눈앞에 있고, 여기에 리드 검색을 넣으면 폼을 열 때마다
 * 대량 PII 조회(감사 로그가 남는 표면)를 하게 된다.
 */
export default function ManualDiagnosisPage() {
  const [submitting, setSubmitting] = useState(false)
  const [created, setCreated] = useState<Created | null>(null)
  const [error, setError] = useState<string | null>(null)
  // 만든 직후 아래 목록이 그 건을 바로 보여줘야 한다 — 그게 이 화면의 확인 수단이다.
  const [reloadToken, setReloadToken] = useState(0)

  async function submit(payload: Record<string, unknown>) {
    setSubmitting(true)
    setError(null)
    try {
      const response = await fetchAPI<{ lead_id: string; diagnosis_id: string }>(
        '/admin/lead-diagnoses',
        { method: 'POST', body: JSON.stringify(payload) },
      )
      setCreated({ leadId: response.lead_id, diagnosisId: response.diagnosis_id })
      setReloadToken((token) => token + 1)
    } catch (caught) {
      // 서버 거절 사유(병원명 혼입, 도입문의 아님 등)는 그대로 보여준다 — 운영자가
      // 고칠 수 있는 입력 문제다.
      const detail =
        caught instanceof ApiError
          ? manualDiagnosisRefusal(caught.detail, caught.status, caught.message)
          : null
      setError(
        detail ??
          safeOperatorError('leads', '입력값을 확인하고 다시 시도해 주세요. 계속 실패하면 개발팀 문의용 정보를 전달하세요.'),
      )
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="p-4 sm:p-6 lg:p-8">
      <header data-current-task className="mb-5">
        <h2 className="text-xl font-bold text-slate-900">노출 진단 생성</h2>
        <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-600 [word-break:keep-all]">
          병원 정보를 직접 넣어 콜용 노출 진단을 만듭니다. 환자 질문 3개를 ChatGPT와 Gemini에
          각각 세 번씩 물어 언급 여부를 정리합니다. 원장님께 자동으로 발송되지 않습니다.
        </p>
      </header>

      {created ? (
        <section className="rounded-xl border border-slate-200 bg-white p-5" role="status">
          <h3 className="font-bold text-slate-900">진단을 접수했습니다</h3>
          <p className="mt-2 text-sm leading-6 text-slate-600 [word-break:keep-all]">
            측정이 끝나면 콜용 보고서가 준비됩니다. 진행 상태는 아래 목록 맨 위에서 바로 확인할 수 있고,
            준비되면 같은 자리에 ‘보고서 열기’가 생깁니다.
          </p>
          <div className="mt-4 flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              onClick={() => setCreated(null)}
              className="min-h-11 rounded-lg bg-blue-600 px-5 text-sm font-bold text-white"
            >
              하나 더 만들기
            </button>
            <Link
              href="/leads"
              className="inline-flex min-h-11 items-center justify-center rounded-lg border border-slate-200 bg-white px-5 text-sm font-bold text-slate-600"
            >
              상담 요청 화면으로
            </Link>
          </div>
        </section>
      ) : (
        <section className="rounded-xl border border-slate-200 bg-white p-5">
          {error && (
            <p className="mb-4 rounded-lg bg-red-50 p-3 text-sm leading-6 text-red-700 [word-break:keep-all]" role="alert">
              {error}
            </p>
          )}
          <ManualDiagnosisForm submitting={submitting} onSubmit={(payload) => void submit(payload)} />
        </section>
      )}

      <DiagnosisHistory reloadToken={reloadToken} />
    </div>
  )
}
