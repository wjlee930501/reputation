'use client'

import { useEffect, useState } from 'react'

import { ApiError, fetchAPI } from '@/lib/api'
import { safeOperatorError } from '@/lib/operations-journey'
import type { Hospital } from '@/types'

interface VerificationValues {
  naver_site_verification: string
  daum_webmaster_verification: string
}

function valuesOf(hospital: Hospital | null): VerificationValues {
  return {
    naver_site_verification: hospital?.naver_site_verification ?? '',
    daum_webmaster_verification: hospital?.daum_webmaster_verification ?? '',
  }
}

export function OwnershipVerificationSection({
  hospital,
  hospitalId,
  onSaved,
}: {
  hospital: Hospital | null
  hospitalId: string
  onSaved: () => void
}) {
  const [form, setForm] = useState<VerificationValues>(() => valuesOf(hospital))
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [feedback, setFeedback] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const serverSignature = JSON.stringify(valuesOf(hospital))

  useEffect(() => {
    if (dirty) return
    setForm(JSON.parse(serverSignature) as VerificationValues)
  }, [serverSignature, dirty])

  function update(field: keyof VerificationValues, value: string) {
    setDirty(true)
    setFeedback(null)
    setForm((current) => ({ ...current, [field]: value }))
  }

  async function save() {
    setSaving(true)
    setFeedback(null)
    setError(null)
    try {
      await fetchAPI(`/admin/hospitals/${hospitalId}/profile`, {
        method: 'PATCH',
        body: JSON.stringify({
          naver_site_verification: form.naver_site_verification.trim() || null,
          daum_webmaster_verification: form.daum_webmaster_verification.trim() || null,
        }),
      })
      setDirty(false)
      setFeedback('검색엔진 소유 확인 값을 저장했습니다.')
      onSaved()
    } catch (caught: unknown) {
      setError(
        caught instanceof ApiError && caught.message
          ? caught.message
          : safeOperatorError('onboarding', '확인 문구 형식을 확인한 뒤 다시 저장해 주세요.'),
      )
    } finally {
      setSaving(false)
    }
  }

  return (
    <section
      id="search-ownership"
      className="scroll-mt-24 space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-6"
    >
      <div>
        <h3 className="text-base font-semibold text-slate-800">검색엔진 소유 확인</h3>
        <p className="mt-0.5 text-xs leading-5 text-slate-500">
          병원별 네이버 코드는 HTML 메타 태그에, 다음 확인 문구는 자기 도메인의 robots.txt
          마지막 줄에 자동 반영됩니다.
        </p>
      </div>

      <div className="grid gap-4">
        <label htmlFor="naver-site-verification" className="text-sm font-medium text-slate-700">
          네이버 소유 확인 코드
          <input
            id="naver-site-verification"
            type="text"
            value={form.naver_site_verification}
            onChange={(event) => update('naver_site_verification', event.target.value)}
            maxLength={255}
            placeholder="content 값만 입력"
            className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm"
          />
          <span className="mt-1 block text-xs font-normal text-slate-500">
            &lt;meta name=&quot;naver-site-verification&quot; content=&quot;…&quot;&gt;의 content 값입니다.
          </span>
        </label>

        <label htmlFor="daum-webmaster-verification" className="text-sm font-medium text-slate-700">
          다음 웹마스터 확인 문구
          <input
            id="daum-webmaster-verification"
            type="text"
            value={form.daum_webmaster_verification}
            onChange={(event) => update('daum_webmaster_verification', event.target.value)}
            maxLength={500}
            placeholder="#DaumWebMasterTool:… 전체 한 줄"
            className="mt-1.5 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm"
          />
          <span className="mt-1 block text-xs font-normal text-slate-500">
            다음 웹마스터 도구가 제공한 #DaumWebMasterTool: 문구 전체를 붙여 넣습니다.
          </span>
        </label>
      </div>

      <p className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600">
        Google 소유 확인은 병원별 값이 아니라 Re:putation 플랫폼 도메인의 배포 환경값으로 관리합니다.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => void save()}
          disabled={saving || !dirty}
          className="inline-flex items-center rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
        >
          {saving ? '저장 중…' : '소유 확인 값 저장'}
        </button>
        {feedback && <span className="text-sm font-semibold text-green-700">{feedback}</span>}
        {error && <span className="text-sm font-semibold text-red-700">{error}</span>}
      </div>
    </section>
  )
}
