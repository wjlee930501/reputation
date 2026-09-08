'use client'

import { useState } from 'react'

import { ApiError, fetchAPI } from '@/lib/api'
import { isExternalLogo, isServableLogo } from '@/lib/clinic-visual-readiness'
import { safeOperatorError } from '@/lib/operations-journey'

/**
 * 공식 로고 — 업로드만 받는다.
 *
 * 예전에는 URL 입력이었다. 그런데 병원 공개 페이지는 우리 저장소·백엔드 오리진이 아닌
 * 주소를 쓰지 않으므로, 병원 홈페이지 CDN 로고를 넣으면 배지만 받고 화면에는 아무
 * 변화가 없었다(L-1). 필수 게이트가 효과 없는 입력을 강제하던 상태라 입력 수단 자체를
 * 바꿨다 — 업로드한 파일은 우리 오리진에서 서빙되므로 상대 CDN 정책과 무관하다.
 */
function shortHost(value: string): string {
  try {
    return new URL(value).hostname
  } catch {
    return '외부 주소'
  }
}

export function ClinicLogoField({
  hospitalId,
  logoUrl,
  onUploaded,
}: {
  hospitalId: string
  logoUrl: string
  onUploaded: () => void
}) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  // 업로드가 저장까지 마쳤다는 즉시 피드백. 다른 필드를 편집 중(dirty)이면 서버 값
  // 동기화가 미뤄지므로, 그 사이에도 방금 한 일이 반영됐음을 보여 준다.
  const [justUploaded, setJustUploaded] = useState(false)
  // 값이 있다는 것과 공개 화면에 뜬다는 것은 다르다 — 외부 주소는 저장돼 있어도
  // 헤더에 아무것도 그리지 못하므로 "업로드됨"이라고 말하면 안 된다(O-5).
  const hasLogo = isServableLogo(logoUrl) || justUploaded
  const needsLogoMigration = !justUploaded && isExternalLogo(logoUrl)

  async function upload(file: File) {
    setUploading(true)
    setUploadError(null)
    try {
      const body = new FormData()
      body.append('file', file)
      // logo_url은 이 엔드포인트가 소유한다 — 시각 요소 폼은 이 필드를 보내지 않는다.
      // 폼이 공개 라우트 주소를 되돌려 저장하면 저장된 자산 참조를 덮어쓰게 된다.
      await fetchAPI(`/admin/hospitals/${hospitalId}/logo`, { method: 'POST', body })
      setJustUploaded(true)
      onUploaded()
    } catch (e: unknown) {
      setUploadError(
        e instanceof ApiError && e.message
          ? e.message
          : safeOperatorError('onboarding', '로고 파일을 다시 선택해 주세요.'),
      )
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="text-sm font-medium text-slate-700">
      공식 로고 이미지
      <div className="mt-1.5 flex items-center gap-3">
        {hasLogo ? (
          <span className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-2 py-1 text-xs font-semibold text-green-700">
            업로드됨
          </span>
        ) : needsLogoMigration ? (
          <span className="inline-flex items-center rounded-lg border border-amber-300 bg-amber-50 px-2 py-1 text-xs font-semibold text-amber-800">
            외부 주소 — 화면에 안 뜸
          </span>
        ) : (
          <span className="inline-flex items-center rounded-lg border border-dashed border-slate-300 px-2 py-1 text-xs text-slate-500">
            아직 없음
          </span>
        )}
        <label className="inline-flex min-h-11 cursor-pointer items-center rounded-lg border border-slate-300 bg-white px-3 text-xs font-semibold text-slate-700 hover:bg-slate-50">
          {uploading ? '업로드 중…' : hasLogo ? '다른 파일로 교체' : '로고 파일 선택'}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            disabled={uploading}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              // 같은 파일을 다시 고를 수 있게 값을 비운다 — 실패 후 재시도가 막히지 않는다.
              e.target.value = ''
              if (file) void upload(file)
            }}
          />
        </label>
      </div>
      {needsLogoMigration && (
        <p className="mt-1.5 rounded-lg bg-amber-50 px-2.5 py-2 text-xs leading-5 text-amber-900">
          예전에 등록한 로고 주소가 외부 사이트({shortHost(logoUrl)})를 가리킵니다.
          병원 공개 페이지는 외부 주소의 이미지를 쓰지 않아 지금 헤더에는 병원명만 나옵니다.
          같은 로고 파일을 업로드하면 바로 반영됩니다.
        </p>
      )}
      <span className="mt-1 block text-xs font-normal text-slate-500">
        PNG·JPG·WEBP, 1MB 이하. 업로드한 파일은 병원 공개 페이지에서 직접 제공되므로
        외부 사이트 주소와 달리 상대 사이트가 바뀌어도 깨지지 않습니다.
      </span>
      {uploadError && <p className="mt-1 text-xs font-semibold text-red-700">{uploadError}</p>}
    </div>
  )
}
