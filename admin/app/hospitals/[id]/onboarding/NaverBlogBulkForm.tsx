'use client'

import { useEffect, useState } from 'react'

import { fetchAPI } from '@/lib/api'
import { OperatorIssuePanel } from '@/app/_components/OperatorIssuePanel'
import { safeOperatorError } from '@/lib/operations-journey'
import {
  buildNaverDeveloperContext,
  isNaverEvidenceAvailable,
  naverItemCopy,
  parseNaverOpenFailures,
  parseNaverHandoffResponse,
  type NaverHandoffItem,
} from '@/lib/naver-handoff'
import NaverHandoffResultItem from './NaverHandoffResultItem'

interface NaverBlogBulkFormProps {
  hospitalId: string
  onCreated: () => void
}

interface Feedback {
  message: string
  isError: boolean
}

export default function NaverBlogBulkForm({ hospitalId, onCreated }: NaverBlogBulkFormProps) {
  const [url, setUrl] = useState('')
  const [maxPosts, setMaxPosts] = useState(5)
  const [busy, setBusy] = useState(false)
  const [retryingHash, setRetryingHash] = useState<string | null>(null)
  const [items, setItems] = useState<NaverHandoffItem[]>([])
  const [feedback, setFeedback] = useState<Feedback | null>(null)

  useEffect(() => {
    let active = true
    fetchAPI<unknown>(`/admin/hospitals/${hospitalId}/essence/sources/crawl-blog/failures`)
      .then((raw) => {
        if (active) setItems(parseNaverOpenFailures(raw))
      })
      .catch((error: unknown) => {
        if (active) setFeedback({ message: errorMessage(error), isError: true })
      })
    return () => { active = false }
  }, [hospitalId])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setFeedback(null)
    try {
      const raw = await fetchAPI<unknown>(`/admin/hospitals/${hospitalId}/essence/sources/crawl-blog`, {
        method: 'POST',
        body: JSON.stringify({ url, max_posts: maxPosts }),
      })
      const result = parseNaverHandoffResponse(raw)
      setItems(result.items)
      setUrl('')
      setFeedback({ message: summaryMessage(result), isError: false })
      onCreated()
    } catch (error: unknown) {
      setFeedback({ message: errorMessage(error), isError: true })
    } finally {
      setBusy(false)
    }
  }

  async function retry(item: NaverHandoffItem) {
    setRetryingHash(item.urlHash)
    setFeedback(null)
    try {
      const raw = await fetchAPI<unknown>(
        `/admin/hospitals/${hospitalId}/essence/sources/crawl-blog/runs/${item.runId}/items/${item.urlHash}/retry`,
        { method: 'POST', body: '{}' },
      )
      const result = parseNaverHandoffResponse(raw)
      const retried = result.items[0]
      if (!retried) throw new Error('다시 수집한 결과를 확인할 수 없습니다. 개발팀에 문의해 주세요.')
      setItems((current) => current.map((candidate) => candidate.urlHash === item.urlHash ? retried : candidate))
      const copy = naverItemCopy(retried)
      const evidenceAvailable = isNaverEvidenceAvailable(retried)
      setFeedback({ message: `다시 수집 결과: ${copy.label}. ${copy.action}`, isError: !evidenceAvailable })
      if (evidenceAvailable) onCreated()
    } catch (error: unknown) {
      setFeedback({ message: errorMessage(error), isError: true })
    } finally {
      setRetryingHash(null)
    }
  }

  async function copyDeveloperContext(item: NaverHandoffItem) {
    try {
      await navigator.clipboard.writeText(buildNaverDeveloperContext({
        hospitalId,
        runId: item.runId,
        urlHash: item.urlHash,
        state: item.state,
      }))
      setFeedback({ message: '개발팀 문의 정보가 복사되었습니다. 사내 문의 채널에 붙여 넣어 주세요.', isError: false })
    } catch (_error: unknown) {
      setFeedback({
        message: '자동 복사가 되지 않았습니다. 아래 문의 정보를 직접 선택해 복사한 뒤 개발팀에 전달해 주세요.',
        isError: true,
      })
    }
  }

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4" aria-labelledby="naver-import-heading">
      <div className="space-y-1">
        <h3 id="naver-import-heading" className="text-sm font-bold text-slate-900">네이버 블로그 글 가져오기</h3>
        <p className="break-keep text-sm leading-6 text-slate-600">
          병원 블로그 주소를 입력하면 최근 글을 근거 자료 검토 목록에 추가합니다. 이미 가져온 글은 자동으로 제외합니다.
        </p>
      </div>

      <form onSubmit={submit} className="space-y-3">
        <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_120px]">
          <label className="min-w-0">
            <span className="sr-only">병원 네이버 블로그 주소</span>
            <input
              required
              type="url"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://blog.naver.com/..."
              className="min-h-11 w-full min-w-0 rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-100"
            />
          </label>
          <label>
            <span className="sr-only">가져올 글 수</span>
            <select
              value={maxPosts}
              onChange={(event) => setMaxPosts(Number(event.target.value))}
              className="min-h-11 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm outline-none focus:border-blue-600 focus:ring-2 focus:ring-blue-100"
            >
              {[5, 10, 15].map((count) => (
                <option key={count} value={count}>최근 {count}개</option>
              ))}
            </select>
          </label>
        </div>
        <button
          type="submit"
          disabled={busy}
          className="min-h-11 rounded-lg bg-blue-600 px-4 text-sm font-semibold text-white outline-none hover:bg-blue-700 focus-visible:ring-2 focus-visible:ring-blue-600 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? '글을 가져오는 중…' : '최근 글 가져오기'}
        </button>
      </form>

      {feedback?.isError ? (
        <OperatorIssuePanel message={feedback.message} surface="onboarding" />
      ) : feedback ? (
        <p className={`break-keep text-sm font-medium ${feedback.isError ? 'text-red-700' : 'text-slate-700'}`} role={feedback.isError ? 'alert' : 'status'} aria-live="polite">
          {feedback.message}
        </p>
      ) : null}

      {items.length > 0 && (
        <div className="space-y-3 border-t border-slate-200 pt-4" aria-label="글 가져오기 결과">
          <h4 className="text-sm font-bold text-slate-900">글별 처리 결과</h4>
          <ul className="space-y-3">
            {items.map((item) => <NaverHandoffResultItem
              key={item.urlHash}
              hospitalId={hospitalId}
              item={item}
              retrying={retryingHash === item.urlHash}
              onRetry={() => retry(item)}
              onCopy={() => copyDeveloperContext(item)}
            />)}
          </ul>
        </div>
      )}
    </section>
  )
}

function summaryMessage(result: ReturnType<typeof parseNaverHandoffResponse>): string {
  const failedCount = result.items.filter((item) => item.state === 'FAILED').length
  const parts = [
    `${result.created}개 글을 근거 자료 검토 목록에 추가했습니다.`,
    `이미 가져온 글 ${result.skippedDuplicate}개`,
    `본문 확인이 필요한 글 ${result.skippedEmpty}개`,
  ]
  if (failedCount > 0) parts.push(`수집하지 못한 글 ${failedCount}개`)
  return parts.join(' · ')
}

function errorMessage(_error: unknown): string {
  return safeOperatorError('onboarding', '네이버 블로그 주소를 확인한 뒤 ‘최근 글 가져오기’를 다시 누르세요.')
}
