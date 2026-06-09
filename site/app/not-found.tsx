import Link from 'next/link'

// 브랜드된 404 — notFound() 호출 시 Next 기본 화면 대신 노출.
export default function NotFound() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 px-6">
      <div className="text-center max-w-md">
        <p className="text-sm font-semibold text-blue-600 mb-2">404</p>
        <h1 className="text-2xl font-bold text-slate-800 mb-3">페이지를 찾을 수 없습니다</h1>
        <p className="text-slate-500 mb-8 text-sm leading-relaxed">
          주소가 잘못되었거나 페이지가 이동·삭제되었을 수 있습니다.
        </p>
        <Link
          href="/"
          className="inline-block bg-blue-600 text-white px-6 py-2.5 rounded-lg font-medium hover:bg-blue-700 transition-colors"
        >
          홈으로 이동
        </Link>
      </div>
    </div>
  )
}
