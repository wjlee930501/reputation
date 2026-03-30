'use client'

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <h2 className="text-xl font-bold text-gray-800 mb-4">오류가 발생했습니다</h2>
        <p className="text-gray-500 mb-6 text-sm">{error.message || '알 수 없는 오류'}</p>
        <button
          onClick={reset}
          className="bg-blue-600 text-white px-6 py-2 rounded-lg font-medium hover:bg-blue-700 transition-colors"
        >
          다시 시도
        </button>
      </div>
    </div>
  )
}
