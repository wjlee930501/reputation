'use client'

// 공개 표면 에러 경계 — 백엔드 일시 장애(ISR 렌더 중 5xx 등)가 Next 기본 에러 화면
// 대신 브랜드된 안내로 노출되도록 한다. 에러 메시지 원문은 노출하지 않는다.
export default function SiteError({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 px-6">
      <div className="text-center max-w-md">
        <h1 className="text-2xl font-bold text-slate-800 mb-3">페이지를 불러오지 못했습니다</h1>
        <p className="text-slate-500 mb-8 text-sm leading-relaxed">
          일시적인 문제로 정보를 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.
        </p>
        <button
          onClick={reset}
          className="bg-blue-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-blue-700 transition-colors"
        >
          다시 시도
        </button>
      </div>
    </div>
  )
}
