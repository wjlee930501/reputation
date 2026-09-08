import {
  buildNaverDeveloperContext,
  naverItemCopy,
  type NaverHandoffItem,
  type NaverItemCopy,
} from '@/lib/naver-handoff'

const TONE_CLASSES: Record<NaverItemCopy['tone'], string> = {
  success: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
  neutral: 'bg-slate-100 text-slate-700 ring-slate-200',
  warning: 'bg-amber-50 text-amber-900 ring-amber-200',
  danger: 'bg-red-50 text-red-800 ring-red-200',
}

interface NaverHandoffResultItemProps {
  hospitalId: string
  item: NaverHandoffItem
  retrying: boolean
  onRetry: () => void
  onCopy: () => void
}

export default function NaverHandoffResultItem({
  hospitalId,
  item,
  retrying,
  onRetry,
  onCopy,
}: NaverHandoffResultItemProps) {
  const copy = naverItemCopy(item)
  const needsDeveloperContext = item.state === 'FAILED'
    || (item.state === 'SKIPPED' && item.safeErrorCode !== 'DUPLICATE_SOURCE')

  return (
    <li className="min-w-0 rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-2">
          <span className={`inline-flex rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset ${TONE_CLASSES[copy.tone]}`}>
            {copy.label}
          </span>
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            title={item.url}
            className="block min-h-11 break-all py-2 text-sm font-medium leading-5 text-blue-700 underline decoration-blue-300 underline-offset-2 hover:text-blue-900 focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-600"
          >
            {item.url}
          </a>
          <p className="break-keep text-sm leading-6 text-slate-700">영향: {copy.impact}</p>
          <p className="break-keep text-sm font-medium leading-6 text-slate-900">다음 행동: {copy.action}</p>
        </div>
        {item.state === 'FAILED' && (
          <button
            type="button"
            disabled={retrying}
            onClick={onRetry}
            className="min-h-11 shrink-0 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white outline-none hover:bg-blue-700 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {retrying ? '다시 수집 중…' : '실패한 글 다시 수집'}
          </button>
        )}
      </div>
      {needsDeveloperContext && (
        <div className="mt-4 border-t border-slate-100 pt-4">
          <p className="break-keep text-xs leading-5 text-slate-500">재시도 후에도 해결되지 않으면 원문 대신 아래 안전한 정보만 개발팀에 전달하세요.</p>
          <pre className="mt-2 max-w-full whitespace-pre-wrap break-all rounded-lg bg-slate-900 p-3 text-xs leading-5 text-slate-100">{buildNaverDeveloperContext({
            hospitalId,
            runId: item.runId,
            urlHash: item.urlHash,
            state: item.state,
          })}</pre>
          <button
            type="button"
            onClick={onCopy}
            className="mt-3 min-h-11 rounded-lg border border-slate-300 bg-white px-4 text-sm font-semibold text-slate-800 outline-none hover:bg-slate-50 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2"
          >
            개발팀 문의 정보 복사
          </button>
        </div>
      )}
    </li>
  )
}
